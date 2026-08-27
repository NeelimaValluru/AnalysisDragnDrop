"""Headless command line interface for AnalysisGUI.

This module is the machine-facing entry point to the pipeline engine: it is
what an editor extension shells out to.  It imports only
:mod:`analysis_gui.pipeline` (standard library plus its own siblings) and must
never import :mod:`analysis_gui.ui`, :mod:`analysis_gui.main`, PyQt6 or
anything else needing a display, so it works over SSH, in Codespaces and in
web VS Code.

Output contract:
  * Machine output goes to stdout as a single JSON object.  The one exception
    is ``codegen`` without ``-o``/``--json``, which writes the generated Python
    to stdout so it can be piped straight into a file.
  * Human-readable messages and errors go to stderr.
  * Every JSON payload carries ``schema_version`` and ``analysis_gui_version``
    so a client can detect version skew.

  ``run`` follows the same split: the generated script's own prints, matplotlib
  notes and tracebacks stream on stderr; a JSON receipt is written to stdout
  *and* to ``<pipeline-stem>.run.json`` beside the pipeline (or ``--receipt``).
  See :mod:`analysis_gui.pipeline.receipt` for the field list.

Matplotlib:
  Generated visualizer code still calls ``plt.show()``, which is what you want
  when exporting a script and running it interactively.  ``run`` executes that
  code in a subprocess with ``MPLBACKEND=Agg`` and wraps ``plt.show`` so each
  call writes a PNG (``<pipeline-stem>_fig_<n>.png``) next to the pipeline
  file — or in ``--cwd`` when that is given — instead of opening a window.
  Headless and CI runs therefore finish instead of blocking on a display.

Exit codes:
  0  success
  1  failure (unreadable file, invalid pipeline, code generation error,
     or the generated script exiting nonzero)
  2  usage error (argparse)
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TextIO, Tuple

from . import __version__
from .pipeline import (
    PORT_AMBIGUOUS,
    PORT_DATA_KINDS,
    PORT_NONE_DECLARED,
    PORT_UNKNOWN,
    SCHEMA_VERSION,
    NEURAL_ANALYSIS_SIGNAL_TYPES,
    CodeGenerator,
    NodePort,
    NodeType,
    PipelineGraph,
    PortSet,
    describe_node_kinds,
    ports_for,
)
from .pipeline.receipt import (
    RECEIPT_SCHEMA_VERSION,
    canonical_json_hash,
    collect_input_files,
    collect_model_summaries,
    compare_environment,
    default_receipt_path,
    environment_is_strict,
    git_commit,
    pipeline_environment,
    sha256_text,
    snapshot_environment,
    utc_now,
    write_receipt,
)

EXIT_OK = 0
EXIT_FAILURE = 1

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


class CliError(Exception):
    """An error that should be reported as a JSON payload and a nonzero exit."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _envelope(**payload: Any) -> Dict[str, Any]:
    """Wrap a payload with the fields every response carries."""
    envelope: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_gui_version": __version__,
    }
    envelope.update(payload)
    return envelope


def _emit_json(payload: Dict[str, Any], stream: TextIO) -> None:
    """Write a JSON payload as a single line-terminated object."""
    json.dump(payload, stream, indent=2, sort_keys=False, default=str)
    stream.write("\n")


def _read_pipeline_document(path: str) -> Dict[str, Any]:
    """Read a ``.pipeline`` file and return its raw JSON document."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise CliError("file_not_found", f"No such pipeline file: {path}")
    except IsADirectoryError:
        raise CliError("file_not_found", f"Not a file: {path}")
    except OSError as exc:
        raise CliError("file_unreadable", f"Could not read {path}: {exc}")
    except json.JSONDecodeError as exc:
        raise CliError("invalid_json", f"{path} is not valid JSON: {exc}")

    if not isinstance(data, dict):
        raise CliError(
            "malformed_document",
            f"{path} must contain a JSON object, found {type(data).__name__}",
        )
    return data


def _build_graph(data: Dict[str, Any]) -> PipelineGraph:
    """Build a graph from a raw document, converting failures to CliError."""
    try:
        return PipelineGraph.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise CliError("malformed_document", f"Could not load pipeline: {exc}")


def _finding(severity: str, code: str, message: str, **context: Any) -> Dict[str, Any]:
    """Build one structured validation finding."""
    finding = {"severity": severity, "code": code, "message": message}
    finding.update(context)
    return finding


def _port_names(ports: Sequence[NodePort]) -> str:
    """Render a port list for a message."""
    return ", ".join(port.name for port in ports) or "none"


def _port_finding(
    index: int,
    node_id: str,
    direction: str,
    port_name: Optional[str],
    resolution_status: str,
    ports: PortSet,
) -> Optional[Dict[str, Any]]:
    """Report one edge endpoint that does not land on a declared port.

    Naming a port the node does not declare is an error: the reference is
    simply wrong, and a canvas has no handle to draw it against.  Leaving the
    port unset on a node with several is only a warning, because that is what
    every document written before ports existed looks like; code generation
    still has a defined answer for it (it consumes the upstream node's whole
    result, as it always did), so the pipeline runs, it is just no longer
    saying which output it means.
    """
    declared = ports.inputs if direction == "input" else ports.outputs
    verb = "targets" if direction == "input" else "leaves"

    if resolution_status == PORT_UNKNOWN:
        return _finding(
            SEVERITY_ERROR,
            "unknown_port",
            f"Edge {index} names {direction} port {port_name!r}, which node "
            f"'{node_id}' does not declare (declares: {_port_names(declared)})",
            node_id=node_id,
            edge_index=index,
        )

    if resolution_status == PORT_NONE_DECLARED:
        return _finding(
            SEVERITY_ERROR,
            "unknown_port",
            f"Edge {index} {verb} node '{node_id}', which declares no "
            f"{direction} ports",
            node_id=node_id,
            edge_index=index,
        )

    if resolution_status == PORT_AMBIGUOUS:
        return _finding(
            SEVERITY_WARNING,
            "ambiguous_port",
            f"Edge {index} does not name an {direction} port on node "
            f"'{node_id}', which declares {len(declared)}: "
            f"{_port_names(declared)}",
            node_id=node_id,
            edge_index=index,
        )

    return None


def _edge_endpoints(
    edge_data: Any,
) -> Optional[Tuple[Any, Any]]:
    """Return ``(source, target)`` from a raw edge, or ``None`` if unreadable."""
    if isinstance(edge_data, dict):
        source, target = edge_data.get("source"), edge_data.get("target")
        if source is None or target is None:
            return None
        return source, target
    if isinstance(edge_data, (list, tuple)) and len(edge_data) == 2:
        return edge_data[0], edge_data[1]
    return None


def _ancestor_signal_types(nodes: Dict[str, Any], edges: List[Any], start: str) -> set:
    """Collect ``metadata.signal_type`` values on ancestors of ``start``."""
    incoming: Dict[str, List[str]] = {}
    for edge_data in edges:
        endpoints = _edge_endpoints(edge_data)
        if endpoints is None:
            continue
        source, target = endpoints
        incoming.setdefault(target, []).append(source)

    found: set = set()
    seen = set()
    stack = list(incoming.get(start, []))
    while stack:
        node_id = stack.pop()
        if node_id in seen or node_id not in nodes:
            continue
        seen.add(node_id)
        node_data = nodes[node_id]
        if isinstance(node_data, dict):
            metadata = node_data.get("metadata") or {}
            if isinstance(metadata, dict):
                signal_type = metadata.get("signal_type")
                if isinstance(signal_type, str) and signal_type:
                    found.add(signal_type)
        stack.extend(incoming.get(node_id, []))
    return found


def _neural_variant(node_data: Dict[str, Any]) -> Optional[str]:
    """Return the neural analysis variant id, or ``None`` if this is not one."""
    metadata = node_data.get("metadata")
    if not isinstance(metadata, dict):
        return None
    for key in ("processor_type", "analyzer_type"):
        value = metadata.get(key)
        if value in NEURAL_ANALYSIS_SIGNAL_TYPES:
            return value
    return None


def collect_findings(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Run structural checks against a raw pipeline document.

    These run on the raw JSON rather than a loaded graph because most of the
    interesting corruption (a node id that disagrees with its map key, an edge
    pointing at a deleted node, an unknown ``node_type``) is either rejected or
    silently dropped by :meth:`PipelineGraph.from_dict`.

    Port checks are skipped for a node whose ``node_type`` is unknown: the
    unknown type is already reported, and there is nothing to check ports
    against.
    """
    findings: List[Dict[str, Any]] = []
    node_ports: Dict[str, PortSet] = {}

    nodes = data.get("nodes", {})
    if not isinstance(nodes, dict):
        findings.append(
            _finding(
                SEVERITY_ERROR,
                "malformed_nodes",
                f"'nodes' must be an object, found {type(nodes).__name__}",
            )
        )
        nodes = {}

    known_types = {member.value for member in NodeType}
    for node_key, node_data in nodes.items():
        if not isinstance(node_data, dict):
            findings.append(
                _finding(
                    SEVERITY_ERROR,
                    "malformed_node",
                    f"Node '{node_key}' must be an object",
                    node_id=node_key,
                )
            )
            continue

        node_id = node_data.get("id")
        if node_id is None:
            findings.append(
                _finding(
                    SEVERITY_ERROR,
                    "missing_node_id",
                    f"Node '{node_key}' has no 'id'",
                    node_id=node_key,
                )
            )
        elif node_id != node_key:
            findings.append(
                _finding(
                    SEVERITY_ERROR,
                    "node_id_mismatch",
                    f"Node stored under key '{node_key}' declares id '{node_id}'",
                    node_id=node_key,
                )
            )

        node_type = node_data.get("node_type")
        if node_type not in known_types:
            findings.append(
                _finding(
                    SEVERITY_ERROR,
                    "unknown_node_type",
                    f"Node '{node_key}' has unknown node_type {node_type!r}",
                    node_id=node_key,
                )
            )
        else:
            metadata = node_data.get("metadata")
            node_ports[node_key] = ports_for(
                node_type, metadata if isinstance(metadata, dict) else None
            )

        parameters = node_data.get("parameters", {})
        if not isinstance(parameters, dict):
            findings.append(
                _finding(
                    SEVERITY_ERROR,
                    "malformed_parameters",
                    f"Node '{node_key}' has non-object 'parameters'",
                    node_id=node_key,
                )
            )

        if node_type == NodeType.CUSTOM_CODE.value:
            findings.extend(_custom_code_path_findings(node_key, node_data))

    edges = data.get("edges", [])
    if not isinstance(edges, list):
        findings.append(
            _finding(
                SEVERITY_ERROR,
                "malformed_edges",
                f"'edges' must be a list, found {type(edges).__name__}",
            )
        )
        edges = []

    seen_edges = set()
    connected_inputs: Dict[str, set] = {}
    for index, edge_data in enumerate(edges):
        source: Optional[str]
        target: Optional[str]
        source_port: Optional[str]
        target_port: Optional[str]
        if isinstance(edge_data, dict):
            source = edge_data.get("source")
            target = edge_data.get("target")
            source_port = edge_data.get("source_port")
            target_port = edge_data.get("target_port")
        elif isinstance(edge_data, (list, tuple)) and len(edge_data) == 2:
            source, target = edge_data[0], edge_data[1]
            source_port = target_port = None
        else:
            findings.append(
                _finding(
                    SEVERITY_ERROR,
                    "malformed_edge",
                    f"Edge {index} is neither an edge object nor a [source, target] pair",
                    edge_index=index,
                )
            )
            continue

        if source is None or target is None:
            findings.append(
                _finding(
                    SEVERITY_ERROR,
                    "malformed_edge",
                    f"Edge {index} is missing 'source' or 'target'",
                    edge_index=index,
                )
            )
            continue

        if source not in nodes:
            findings.append(
                _finding(
                    SEVERITY_ERROR,
                    "dangling_edge",
                    f"Edge {index} references missing source node '{source}'",
                    edge_index=index,
                    node_id=source,
                )
            )
        if target not in nodes:
            findings.append(
                _finding(
                    SEVERITY_ERROR,
                    "dangling_edge",
                    f"Edge {index} references missing target node '{target}'",
                    edge_index=index,
                    node_id=target,
                )
            )
        if source == target:
            findings.append(
                _finding(
                    SEVERITY_ERROR,
                    "self_loop",
                    f"Edge {index} connects node '{source}' to itself",
                    edge_index=index,
                    node_id=source,
                )
            )

        source_ports = node_ports.get(source)
        source_resolved = None
        if source_ports is not None:
            source_resolved = source_ports.resolve_output(source_port)
            finding = _port_finding(
                index,
                source,
                "output",
                source_port,
                source_resolved.status,
                source_ports,
            )
            if finding is not None:
                findings.append(finding)

        target_ports = node_ports.get(target)
        target_resolved = None
        if target_ports is not None:
            target_resolved = target_ports.resolve_input(target_port)
            finding = _port_finding(
                index,
                target,
                "input",
                target_port,
                target_resolved.status,
                target_ports,
            )
            if finding is not None:
                findings.append(finding)
            if target_resolved.port is not None:
                connected_inputs.setdefault(target, set()).add(
                    target_resolved.port.name
                )

        if (
            source_resolved is not None
            and target_resolved is not None
            and source_resolved.ok
            and target_resolved.ok
            and not target_resolved.port.accepts(source_resolved.port)
        ):
            findings.append(
                _finding(
                    SEVERITY_ERROR,
                    "incompatible_data_kind",
                    f"Edge {index} connects {source_resolved.port.data_kind} "
                    f"to {target_resolved.port.data_kind}, which are not compatible",
                    node_id=target,
                    edge_index=index,
                )
            )

        edge_key = (source, source_port, target, target_port)
        if edge_key in seen_edges:
            findings.append(
                _finding(
                    SEVERITY_WARNING,
                    "duplicate_edge",
                    f"Edge {index} duplicates an earlier edge",
                    edge_index=index,
                )
            )
        seen_edges.add(edge_key)

    # A required input with nothing feeding it is normal in a pipeline someone
    # is still wiring up, so it is a warning: it says the pipeline is not
    # finished, not that the document is wrong.
    for node_key, port_set in node_ports.items():
        connected = connected_inputs.get(node_key, set())
        for port in port_set.required_inputs():
            if port.name not in connected:
                findings.append(
                    _finding(
                        SEVERITY_WARNING,
                        "unconnected_required_input",
                        f"Node '{node_key}' has nothing connected to its "
                        f"required input port '{port.name}'",
                        node_id=node_key,
                    )
                )

    for node_key, node_data in nodes.items():
        if not isinstance(node_data, dict):
            continue
        variant = _neural_variant(node_data)
        if variant is None:
            continue
        allowed = NEURAL_ANALYSIS_SIGNAL_TYPES[variant]
        found = _ancestor_signal_types(nodes, edges, node_key)
        bad = sorted(found - allowed)
        if not bad:
            continue
        findings.append(
            _finding(
                SEVERITY_ERROR,
                "incompatible_signal_type",
                f"Node '{node_key}' ({variant}) cannot analyze {', '.join(bad)} "
                f"data (accepted: {', '.join(sorted(allowed))})",
                node_id=node_key,
            )
        )

    return findings


def validate_document(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a raw pipeline document and return a JSON-ready report."""
    findings = collect_findings(data)
    blocking = any(f["severity"] == SEVERITY_ERROR for f in findings)

    node_count = len(data.get("nodes", {}) or {})
    edge_count = len(data.get("edges", []) or [])

    if not blocking:
        graph = _build_graph(data)
        node_count = len(graph.nodes)
        edge_count = len(graph.edges)
        graph_valid, message = graph.is_valid()
        if not graph_valid:
            code = "empty_pipeline" if not graph.nodes else "invalid_graph"
            if "cycle" in message.lower():
                code = "cycle_detected"
            findings.append(_finding(SEVERITY_ERROR, code, message))

    errors = [f for f in findings if f["severity"] == SEVERITY_ERROR]
    warnings = [f for f in findings if f["severity"] == SEVERITY_WARNING]

    return {
        "valid": not errors,
        "file_schema_version": PipelineGraph.schema_version_of(data),
        "node_count": node_count,
        "edge_count": edge_count,
        "findings": findings,
        "summary": {"errors": len(errors), "warnings": len(warnings)},
    }


def cmd_codegen(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    """Generate Python source from a pipeline file."""
    data = _read_pipeline_document(args.pipeline)
    graph = _build_graph(data)

    try:
        code = CodeGenerator(graph).generate()
    except ValueError as exc:
        raise CliError("codegen_failed", str(exc))

    output_path = args.output
    if output_path:
        try:
            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write(code + "\n")
        except OSError as exc:
            raise CliError("write_failed", f"Could not write {output_path}: {exc}")
        print(f"Wrote generated pipeline code to {output_path}", file=stderr)

    if args.json or output_path:
        payload = _envelope(
            status="ok",
            command="codegen",
            file=args.pipeline,
            file_schema_version=PipelineGraph.schema_version_of(data),
            output_path=output_path,
            line_count=len(code.splitlines()),
        )
        if args.json:
            payload["code"] = code
        _emit_json(payload, stdout)
    else:
        stdout.write(code + "\n")

    return EXIT_OK


def cmd_validate(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    """Validate a pipeline file and emit structured findings."""
    data = _read_pipeline_document(args.pipeline)
    report = validate_document(data)

    payload = _envelope(
        status="ok" if report["valid"] else "error",
        command="validate",
        file=args.pipeline,
        **report,
    )
    _emit_json(payload, stdout)

    if not report["valid"]:
        for finding in report["findings"]:
            if finding["severity"] == SEVERITY_ERROR:
                print(f"error: {finding['message']}", file=stderr)
        return EXIT_FAILURE

    return EXIT_OK


def _custom_code_path_findings(
    node_id: str, node_data: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Report a discovered function whose module or source file is gone."""
    metadata = node_data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    parameters = node_data.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}

    def _resolved(key: str) -> str:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return os.path.expanduser(value)
        param = parameters.get(key)
        if isinstance(param, dict):
            raw = param.get("value")
            if raw is None:
                raw = param.get("default_value")
            if isinstance(raw, str) and raw.strip():
                return os.path.expanduser(raw)
        return ""

    source_path = _resolved("source_path")
    if source_path and not os.path.isfile(source_path):
        return [
            _finding(
                SEVERITY_ERROR,
                "missing_module_path",
                f"Node '{node_id}' points at missing source {source_path!r}",
                node_id=node_id,
            )
        ]

    library_root = _resolved("library_root")
    if library_root and not os.path.isdir(library_root):
        return [
            _finding(
                SEVERITY_ERROR,
                "missing_module_path",
                f"Node '{node_id}' library root does not exist: {library_root}",
                node_id=node_id,
            )
        ]
    return []


def cmd_describe(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    """Emit the registry of constructible node kinds.

    Discovered ("Your code") kinds are omitted by default so the palette stays
    small.  Pass ``--include-discovered`` or set ``ANALYSIS_GUI_DISCOVER=1`` to
    attach ``discovered_kinds``.  Without ``--root``, only the workspace
    ``src/`` directory is scanned — not registered repos or the rest of the
    disk.
    """
    payload = _envelope(
        status="ok",
        command="describe",
        node_types=[member.value for member in NodeType],
        port_data_kinds=list(PORT_DATA_KINDS),
        node_kinds=describe_node_kinds(),
    )
    if _include_discovered(args):
        roots = _describe_discovery_roots(args)
        if roots:
            from .repository import discover_libraries

            index = discover_libraries(roots=roots, workspace=args.workspace)
            payload["discovered_kinds"] = index["kinds"]
            payload["discovered_count"] = index["count"]
            payload["discovered_roots"] = index["roots"]
            payload["chunk_counts"] = index["chunk_counts"]
        else:
            payload["discovered_kinds"] = []
            payload["discovered_count"] = 0
            payload["discovered_roots"] = []
            payload["chunk_counts"] = {}
    _emit_json(payload, stdout)
    return EXIT_OK


def _include_discovered(args: argparse.Namespace) -> bool:
    if getattr(args, "include_discovered", False):
        return True
    flag = os.environ.get("ANALYSIS_GUI_DISCOVER", "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def _describe_discovery_roots(args: argparse.Namespace) -> List[str]:
    """Workspace ``src/`` only when ``--root`` is omitted (do not scan the world)."""
    if args.root:
        return list(args.root)
    workspace = os.path.abspath(os.path.expanduser(args.workspace or os.getcwd()))
    src = os.path.join(workspace, "src")
    if os.path.isdir(src):
        return [src]
    return []


def cmd_discover(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    """Scan configured library roots and emit candidate node kinds."""
    from .repository import discover_libraries

    index = discover_libraries(roots=args.root, workspace=args.workspace)
    payload = _envelope(
        status="ok",
        command="discover",
        roots=index["roots"],
        functions=index["functions"],
        kinds=index["kinds"],
        count=index["count"],
        chunk_counts=index["chunk_counts"],
        errors=index["errors"],
    )
    _emit_json(payload, stdout)
    counts = index["chunk_counts"]
    print(
        f"Indexed {index['count']} chunks "
        f"({counts.get('function', 0)} functions, "
        f"{counts.get('method', 0)} methods, "
        f"{counts.get('block', 0)} blocks) "
        f"in {len(index['roots'])} root(s)",
        file=stderr,
    )
    return EXIT_OK


def cmd_similar(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    """Rank discovered library chunks for a free-text or chunk query."""
    from .repository import find_similar
    from .repository.matching import RANKER_LEGACY, parse_span_spec

    query = getattr(args, "query", None) or ""
    from_span = getattr(args, "from_span", None) or None
    from_kind = getattr(args, "from_kind", None) or None
    sources = sum(bool(item) for item in (query.strip(), from_span, from_kind))
    if sources == 0:
        raise CliError(
            "usage",
            "similar requires a query, --from-span PATH:START-END, or --from-kind KIND",
        )
    if from_span:
        try:
            parse_span_spec(from_span)
        except ValueError as exc:
            raise CliError("usage", str(exc)) from exc

    ranker = RANKER_LEGACY if getattr(args, "legacy_tfidf", False) else "api_intent"
    result = find_similar(
        query,
        roots=args.root,
        workspace=args.workspace,
        limit=args.limit,
        ranker=ranker,
        from_span=from_span,
        from_kind=from_kind,
    )
    label = result["query"]
    payload = _envelope(
        status="ok",
        command="similar",
        query=label,
        roots=result["roots"],
        hits=result["hits"],
        count=result["count"],
        indexed=result["indexed"],
        chunk_counts=result.get("chunk_counts", {}),
        reranked=result["reranked"],
        ranker=result.get("ranker"),
        candidates_examined=result.get("candidates_examined"),
        used_fallback=result.get("used_fallback", False),
        alignments_scored=result.get("alignments_scored", 0),
        from_span=from_span,
        from_kind=from_kind,
        errors=result["errors"],
    )
    _emit_json(payload, stdout)
    print(
        f"Ranked {result['count']} of {result['indexed']} chunks for {label!r}"
        f" ({result.get('ranker')}, "
        f"examined {result.get('candidates_examined', result['indexed'])})",
        file=stderr,
    )
    return EXIT_OK


#: Injected ahead of generated pipeline code so ``plt.show()`` cannot block.
#: Lives here, not in :mod:`analysis_gui.pipeline`, so the pipeline package
#: stays free of matplotlib (and of any other runtime dependency).
_RUN_PREAMBLE = """\
import json as _json
import os as _os
import sys as _sys

_os.environ.setdefault("MPLBACKEND", "Agg")
_figure_dir = _os.environ.get("ANALYSIS_GUI_FIGURE_DIR") or _os.getcwd()
_figure_prefix = _os.environ.get("ANALYSIS_GUI_FIGURE_PREFIX") or "pipeline"
_figure_manifest = _os.environ.get("ANALYSIS_GUI_FIGURE_MANIFEST") or ""
_saved_figures = []
_figure_index = 0

try:
    import matplotlib as _matplotlib
    _matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as _plt
except ImportError:
    pass
else:
    def _analysis_gui_show(*_args, **_kwargs):
        global _figure_index
        _figure_index += 1
        _os.makedirs(_figure_dir, exist_ok=True)
        path = _os.path.join(
            _figure_dir, f"{_figure_prefix}_fig_{_figure_index}.png"
        )
        _plt.savefig(path, bbox_inches="tight")
        _plt.close("all")
        _saved_figures.append(_os.path.abspath(path))
        if _figure_manifest:
            with open(_figure_manifest, "w", encoding="utf-8") as handle:
                _json.dump(_saved_figures, handle)
        print(f"Saved figure to {path}", file=_sys.stderr)

    _plt.show = _analysis_gui_show
"""


def _pump_stream(source: TextIO, dest: TextIO) -> None:
    """Copy ``source`` to ``dest`` line by line so the user sees live output."""
    try:
        for line in source:
            dest.write(line)
            dest.flush()
    except (OSError, ValueError):
        return


def _read_saved_figures(manifest_path: str) -> List[str]:
    """Read the figure list written by the injected matplotlib wrapper."""
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, str)]


def cmd_run(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    """Generate pipeline code and execute it in a subprocess.

    The child uses this same interpreter so the environment that can import
    ``analysis_gui`` is the one that runs the generated script.  Working
    directory defaults to the pipeline file's directory so relative CSV paths
    resolve; ``--cwd`` overrides that (the editor uses it for unsaved buffers
    whose scratch copy lives elsewhere).

    After the child exits, a run receipt is written beside the pipeline
    (``<stem>.run.json``) or to ``--receipt``.  See
    :mod:`analysis_gui.pipeline.receipt`.
    """
    started_at = utc_now()
    pipeline_path = Path(args.pipeline).expanduser()
    data = _read_pipeline_document(str(pipeline_path))
    graph = _build_graph(data)

    if args.cwd:
        run_cwd = str(Path(args.cwd).expanduser().resolve())
    else:
        run_cwd = str(pipeline_path.resolve().parent)

    if not os.path.isdir(run_cwd):
        raise CliError("invalid_cwd", f"Working directory does not exist: {run_cwd}")

    declared_env = pipeline_environment(data)
    actual_env = snapshot_environment()
    env_warnings = compare_environment(declared_env, actual_env)
    strict_env = environment_is_strict(declared_env, getattr(args, "strict_env", False))
    for message in env_warnings:
        print(f"warning: environment: {message}", file=stderr)
    if env_warnings and strict_env:
        raise CliError(
            "environment_mismatch",
            "Pinned environment does not match this interpreter: "
            + "; ".join(env_warnings),
        )

    try:
        code = CodeGenerator(graph).generate()
    except ValueError as exc:
        raise CliError("codegen_failed", str(exc))

    figure_prefix = pipeline_path.stem or "pipeline"
    script_file = tempfile.NamedTemporaryFile(
        "w", suffix=".py", prefix="analysis-gui-run-", delete=False, encoding="utf-8"
    )
    manifest_file = tempfile.NamedTemporaryFile(
        "w",
        suffix=".json",
        prefix="analysis-gui-figures-",
        delete=False,
        encoding="utf-8",
    )
    script_path = script_file.name
    manifest_path = manifest_file.name
    script_file.write(_RUN_PREAMBLE)
    script_file.write("\n")
    script_file.write(code)
    if not code.endswith("\n"):
        script_file.write("\n")
    script_file.close()
    manifest_file.close()

    env = os.environ.copy()
    env["MPLBACKEND"] = "Agg"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = env.get("PYTHONIOENCODING", "utf-8")
    env["ANALYSIS_GUI_FIGURE_DIR"] = run_cwd
    env["ANALYSIS_GUI_FIGURE_PREFIX"] = figure_prefix
    env["ANALYSIS_GUI_FIGURE_MANIFEST"] = manifest_path

    print(f"Running generated pipeline from {pipeline_path}", file=stderr)
    print(f"Working directory: {run_cwd}", file=stderr)
    print(
        "Matplotlib: Agg backend; plt.show() saves PNG files next to the pipeline.",
        file=stderr,
    )
    stderr.flush()

    child_code = 1
    try:
        proc = subprocess.Popen(
            [sys.executable, script_path],
            cwd=run_cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if proc.stdout is None or proc.stderr is None:
            raise CliError("run_failed", "Could not capture generated pipeline output")
        threads = [
            threading.Thread(
                target=_pump_stream, args=(proc.stdout, stderr), daemon=True
            ),
            threading.Thread(
                target=_pump_stream, args=(proc.stderr, stderr), daemon=True
            ),
        ]
        for thread in threads:
            thread.start()
        child_code = proc.wait()
        for thread in threads:
            thread.join()
    except OSError as exc:
        raise CliError("run_failed", f"Could not execute generated code: {exc}")
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass

    saved_figures = _read_saved_figures(manifest_path)
    try:
        os.unlink(manifest_path)
    except OSError:
        pass

    receipt_target = getattr(args, "receipt", None) or default_receipt_path(
        str(pipeline_path)
    )
    succeeded = child_code == 0
    payload = _envelope(
        status="ok" if succeeded else "error",
        command="run",
        file=str(pipeline_path),
        file_schema_version=PipelineGraph.schema_version_of(data),
        cwd=run_cwd,
        exit_code=child_code,
        saved_figures=saved_figures,
        interpreter=sys.executable,
        interpreter_version=sys.version,
        started_at=started_at,
        finished_at=utc_now(),
        graph_hash=canonical_json_hash(data),
        generated_code_hash=sha256_text(code),
        input_files=collect_input_files(graph.nodes.values(), run_cwd),
        git_commit=git_commit(str(pipeline_path.resolve().parent)),
        model_summaries=collect_model_summaries(graph.nodes.values()),
        environment=actual_env,
        environment_warnings=env_warnings,
        environment_strict=strict_env,
        receipt_schema_version=RECEIPT_SCHEMA_VERSION,
    )
    if not succeeded:
        payload["error"] = {
            "code": "pipeline_failed",
            "message": f"Generated pipeline exited with code {child_code}",
        }
        print(f"error: generated pipeline exited with code {child_code}", file=stderr)

    try:
        receipt_path = write_receipt(receipt_target, payload)
    except OSError as exc:
        raise CliError(
            "write_failed", f"Could not write receipt {receipt_target}: {exc}"
        )
    payload["receipt_path"] = receipt_path
    payload["output_paths"] = list(saved_figures) + [receipt_path]
    try:
        write_receipt(receipt_path, payload)
    except OSError:
        pass

    _emit_json(payload, stdout)
    return EXIT_OK if succeeded else EXIT_FAILURE


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the headless CLI."""
    parser = argparse.ArgumentParser(
        prog="analysis-gui-cli",
        description=(
            "Headless interface to the AnalysisGUI pipeline engine. "
            "Emits JSON on stdout; human-readable messages on stderr."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"analysis-gui-cli {__version__} (schema v{SCHEMA_VERSION})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    codegen = subparsers.add_parser(
        "codegen", help="Generate Python source from a .pipeline file"
    )
    codegen.add_argument("pipeline", help="Path to a .pipeline file")
    codegen.add_argument(
        "-o",
        "--output",
        default=None,
        help="Write generated code here instead of stdout",
    )
    codegen.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON envelope containing the generated code",
    )
    codegen.set_defaults(func=cmd_codegen)

    validate = subparsers.add_parser(
        "validate", help="Validate a .pipeline file and report findings"
    )
    validate.add_argument("pipeline", help="Path to a .pipeline file")
    validate.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (the default; accepted for explicitness)",
    )
    validate.set_defaults(func=cmd_validate)

    describe = subparsers.add_parser(
        "describe", help="Describe the available node kinds and their parameters"
    )
    describe.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (the default; accepted for explicitness)",
    )
    describe.add_argument(
        "--include-discovered",
        action="store_true",
        help=(
            "'discovered_kinds'.  Also enabled by ANALYSIS_GUI_DISCOVER=1. "
            "Does not add them to the default palette."
        ),
    )
    _add_library_root_args(describe)
    describe.set_defaults(func=cmd_describe)

    discover = subparsers.add_parser(
        "discover",
        help="Scan library roots for analysis-step chunks and candidate node kinds",
    )
    discover.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (the default; accepted for explicitness)",
    )
    _add_library_root_args(discover)
    discover.set_defaults(func=cmd_discover)

    similar = subparsers.add_parser(
        "similar",
        help="Rank discovered library chunks for a free-text or chunk query",
    )
    similar.add_argument(
        "query",
        nargs="?",
        default="",
        help='Code or description to match, e.g. "bandpass eeg filter"',
    )
    similar.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (the default; accepted for explicitness)",
    )
    similar.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of hits to return (default: 20)",
    )
    similar.add_argument(
        "--legacy-tfidf",
        action="store_true",
        help="Use the original brute-force TF-IDF + Jaccard ranker",
    )
    similar.add_argument(
        "--from-span",
        default=None,
        metavar="PATH:START-END",
        help=(
            "Query-by-chunk: use the intent+api+kind of this source span "
            "(e.g. path.py:12-40) instead of a text string"
        ),
    )
    similar.add_argument(
        "--from-kind",
        default=None,
        metavar="KIND",
        help=(
            "Query-by-chunk: use a discovered node kind "
            "(repo.module.func) as the query"
        ),
    )
    _add_library_root_args(similar)
    similar.set_defaults(func=cmd_similar)

    run = subparsers.add_parser(
        "run",
        help="Generate and execute a .pipeline file",
    )
    run.add_argument("pipeline", help="Path to a .pipeline file")
    run.add_argument(
        "--cwd",
        default=None,
        help=(
            "Working directory for the generated script "
            "(default: the pipeline file's directory)"
        ),
    )
    run.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (the default; accepted for explicitness)",
    )
    run.add_argument(
        "--receipt",
        default=None,
        help=(
            "Write the run receipt JSON here "
            "(default: <pipeline-stem>.run.json beside the pipeline)"
        ),
    )
    run.add_argument(
        "--strict-env",
        action="store_true",
        help=(
            "Treat a pinned environment/requires mismatch as an error "
            "(default: warn)"
        ),
    )
    run.set_defaults(func=cmd_run)

    return parser


def _add_library_root_args(parser: argparse.ArgumentParser) -> None:
    """``--root`` / ``--workspace`` shared by discover, similar and describe."""
    parser.add_argument(
        "--root",
        action="append",
        default=None,
        help=(
            "Library directory to scan (repeatable).  When given, these replace "
            "the default workspace src/ plus registered repositories."
        ),
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace whose src/ directory is scanned when --root is omitted",
    )


def main(
    argv: Optional[Sequence[str]] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    """Run the CLI and return a process exit code."""
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args, stdout, stderr)
    except CliError as exc:
        _emit_json(
            _envelope(
                status="error",
                command=args.command,
                error={"code": exc.code, "message": exc.message},
            ),
            stdout,
        )
        print(f"error: {exc.message}", file=stderr)
        return EXIT_FAILURE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
