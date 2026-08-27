"""Turn discovered chunks into candidate custom_code node kinds.

Discovered kinds are *not* registered in :data:`NODE_KINDS`.  That keeps the
palette from growing a button per function in a large library; they show up
through ``discover`` / ``similar`` and optionally ``describe --include-discovered``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from ..pipeline.node import (
    DATA_KIND_ANY,
    Node,
    NodeParameter,
    NodePort,
    NodeType,
    PortSet,
)
from .scan import DATA_ARG_NAMES, DiscoveredFunction, FunctionArg

_LITERAL_STRINGS = re.compile(r"""['"]([^'"]+)['"]""")

#: Parameters that describe *which* function to call, not arguments of it.
CONTROL_PARAM_NAMES = frozenset(
    {"function_name", "module", "library_root", "repository_id"}
)


def annotation_to_param_type(annotation: Optional[str], name: str) -> str:
    """Map a type annotation onto an existing ``NodeParameter.param_type``."""
    lowered_name = name.lower()
    if annotation:
        compact = annotation.replace(" ", "")
        lowered = compact.lower()
        if "literal[" in lowered or lowered.endswith("enum") or ".enum" in lowered:
            return "dropdown"
        if "path" in lowered or "pathlike" in lowered:
            return "file"
        if compact in ("bool", "Boolean"):
            return "boolean"
        if compact in ("int", "float", "complex") or "number" in lowered:
            return "number"
        if compact in ("str", "String"):
            if any(token in lowered_name for token in ("path", "file", "filename")):
                return "file"
            return "string"
        # Unknown annotation → text-like string, matching the existing types.
        return "string"

    if any(token in lowered_name for token in ("path", "file", "filename")):
        return "file"
    return "string"


def literal_options(annotation: Optional[str]) -> List[str]:
    """Extract dropdown options from a ``Literal['a', 'b']`` annotation."""
    if not annotation or "Literal" not in annotation:
        return []
    return _LITERAL_STRINGS.findall(annotation)


def data_arg_name(record: DiscoveredFunction) -> Optional[str]:
    """Name of the first data-like argument, if the function has one."""
    if not record.has_data_input or not record.args:
        return None
    first = record.args[0]
    if first.name.lower() in DATA_ARG_NAMES or record.has_data_input:
        return first.name
    return None


def parameters_for(record: DiscoveredFunction) -> Dict[str, NodeParameter]:
    """Node parameters: how to import the function, plus its non-data args."""
    params: Dict[str, NodeParameter] = {
        "function_name": NodeParameter(
            name="function_name",
            param_type="string",
            default_value=record.name,
            description="Name of the function to call",
        ),
        "module": NodeParameter(
            name="module",
            param_type="string",
            default_value=record.module,
            description="Importable module that contains the function",
        ),
        "library_root": NodeParameter(
            name="library_root",
            param_type="string",
            default_value=record.library_root,
            description="Directory to add to sys.path so the module imports",
        ),
    }
    if record.repository_id:
        params["repository_id"] = NodeParameter(
            name="repository_id",
            param_type="string",
            default_value=record.repository_id,
            description="ID of the user repository",
        )

    skip = data_arg_name(record)
    for arg in record.args:
        if skip and arg.name == skip:
            continue
        param_type = annotation_to_param_type(arg.annotation, arg.name)
        options = literal_options(arg.annotation) if param_type == "dropdown" else []
        description = arg.annotation or ""
        params[arg.name] = NodeParameter(
            name=arg.name,
            param_type=param_type,
            default_value=arg.default if arg.has_default else None,
            description=description,
            options=options,
        )
    return params


def metadata_for(record: DiscoveredFunction) -> Dict[str, Any]:
    """Metadata the code generator and validator read."""
    meta: Dict[str, Any] = {
        "repo": record.repository_id,
        "module": record.module,
        "function": record.name,
        "source_path": record.source_path,
        "library_root": record.library_root,
        "kind": record.kind,
        "has_data_input": record.has_data_input,
        "starred": record.starred,
        "tags": list(record.tags),
        "chunk_kind": record.chunk_kind,
        "lineno": record.lineno,
        "end_lineno": record.end_lineno or record.lineno,
        "source_hash": record.source_hash,
        "preview": record.preview,
        "leading_comment": record.leading_comment,
        "calls": list(record.calls),
    }
    if record.class_name:
        meta["class_name"] = record.class_name
    return meta


def ports_for_record(record: DiscoveredFunction) -> PortSet:
    """One ``data`` in / ``output`` out, unless the signature has no data arg."""
    output = NodePort(
        name="output",
        label="Output",
        data_kind=DATA_KIND_ANY,
        description="Whatever the custom function returns",
    )
    if not record.has_data_input:
        return PortSet(inputs=(), outputs=(output,))
    return PortSet(
        inputs=(
            NodePort(
                name="data",
                label="Data",
                data_kind=DATA_KIND_ANY,
                required=True,
                description="Value passed as the function's first data argument",
            ),
        ),
        outputs=(output,),
    )


def candidate_to_node(record: DiscoveredFunction) -> Node:
    """Build a ``custom_code`` node for ``record``.

    Functions and methods are called by import.  Inline blocks are inlined
    into generated pipeline code; the original repository file is not edited.
    """
    if record.chunk_kind == "block":
        end = record.end_lineno or record.lineno
        description = (
            record.docstring_first_line
            or record.leading_comment
            or f"Inline chunk from {record.source_path}:{record.lineno}-{end}"
        )
    else:
        description = record.docstring_first_line or (
            f"Call {record.qualified_name} from the library"
        )
    node = Node(
        id="",
        node_type=NodeType.CUSTOM_CODE,
        label=record.display_name,
        description=description,
        parameters=parameters_for(record),
        metadata=metadata_for(record),
    )
    return node


def kind_description(record: DiscoveredFunction) -> Dict[str, Any]:
    """JSON description in the same shape as :func:`describe_node_kinds`."""
    node = candidate_to_node(record)
    # Ports are derived from metadata on a live node; build the candidate
    # description from the record so we do not depend on NODE_KINDS.
    return {
        "kind": record.kind,
        "palette_label": record.display_name,
        "in_palette": False,
        "starred": record.starred,
        "node_type": NodeType.CUSTOM_CODE.value,
        "label": node.label,
        "description": node.description,
        "metadata": node.metadata,
        "parameters": [param.to_dict() for param in node.parameters.values()],
        "ports": ports_for_record(record).to_dict(),
        "qualified_name": record.qualified_name,
        "source_path": record.source_path,
        "library_root": record.library_root,
        "tags": list(record.tags),
        "lineno": record.lineno,
        "class_name": record.class_name,
        "chunk_kind": record.chunk_kind,
        "span": record.span,
        "preview": record.preview,
        "source_hash": record.source_hash,
        "leading_comment": record.leading_comment,
        "calls": list(record.calls),
        "name": record.name,
    }


def describe_discovered_kinds(
    records: Sequence[DiscoveredFunction],
) -> List[Dict[str, Any]]:
    """Describe every discovered callable as a candidate node kind."""
    return [kind_description(record) for record in records]
