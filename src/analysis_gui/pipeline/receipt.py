"""Run receipts written by ``analysis-gui-cli run``.

Every successful or failed ``run`` writes a JSON receipt next to the pipeline
file (``<stem>.run.json``) unless ``--receipt`` names another path.  The same
object is also printed on stdout so an editor can parse it without opening the
file.

Receipts are an *additive* document: they reuse the CLI envelope's
``schema_version`` (the ``.pipeline`` format version) and
``analysis_gui_version``.  New keys are optional; readers should use ``.get()``.
The pipeline schema version is **not** bumped for receipts.

Receipt schema (keys on the stdout / ``*.run.json`` object)
----------------------------------------------------------
Required envelope (already emitted by every CLI command)::

    schema_version          int     pipeline document schema (currently 1)
    analysis_gui_version    str     ``analysis_gui.__version__``
    status                  str     ``ok`` or ``error``
    command                 str     ``run``

Run-specific keys::

    file                    str     pipeline path that was executed
    file_schema_version     int     ``version`` declared in that file
    cwd                     str     working directory of the child process
    exit_code               int     child process return code
    interpreter             str     ``sys.executable`` used for the child
    interpreter_version     str     ``sys.version`` of that interpreter
    started_at              str     UTC ISO-8601 timestamp (run start)
    finished_at             str     UTC ISO-8601 timestamp (after child exits)
    graph_hash              str     SHA-256 of canonical pipeline JSON
    generated_code_hash     str     SHA-256 of the generated Python source
    input_files             list    ``{uri, resolved_path, sha256?}`` for each
                                    loader ``file_path`` that resolved to a
                                    readable local file (directories omit hash)
    git_commit              str|null  ``git rev-parse HEAD`` from the pipeline
                                    directory; ``null`` if git is missing, the
                                    path is not a repo, or the command fails
    model_summaries         list    one object per ``model_call`` node:
                                    ``node_id``, ``provider``, ``model``,
                                    ``prompt_preview`` (truncated; never keys)
    saved_figures           list    PNG paths written by the Agg ``plt.show``
                                    wrapper
    output_paths            list    saved figures plus the receipt path
    receipt_path            str     where this object was written
    environment             object  *actual* runtime (see below), not the pin
    environment_warnings    list    mismatch messages against a declared pin
    environment_strict      bool    whether mismatches were treated as errors

``environment`` (actual, always recorded)::

    python          str     major.minor.micro of the interpreter
    analysis_gui    str     installed package version
    extras          object  extra name → whether its import is available
                            (``spike``, ``eeg``, ``neural``, ``models``,
                            ``s3``, ``gcs``)

Optional pin on the pipeline document (absent = no pin, backward compatible)::

    {
      "version": 1,
      "environment": {
        "python": "3.11",
        "analysis_gui": "0.1.0",
        "extras": ["spike", "eeg"],
        "strict": false
      },
      "nodes": {},
      "edges": []
    }

``requires`` is accepted as an alias of ``environment``.  ``run`` warns on
mismatch by default; ``--strict-env`` or ``environment.strict`` /
``on_mismatch: "error"`` makes it a hard failure *before* the child starts.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .. import __version__

#: Independent of :data:`~analysis_gui.pipeline.graph.SCHEMA_VERSION`.
#: Bump only if the receipt *shape* becomes incompatible; new keys do not
#: require a bump.
RECEIPT_SCHEMA_VERSION = 1

_EXTRA_IMPORTS = {
    "spike": "spikeinterface",
    "eeg": "mne",
    "neural": "mne",
    "models": "anthropic",
    "claude": "anthropic",
    "gpt": "openai",
    "s3": "boto3",
    "gcs": "google.cloud.storage",
}

_PROMPT_PREVIEW = 240


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp with a ``Z`` suffix."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def sha256_text(text: str) -> str:
    """SHA-256 hex digest of a Unicode string (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str, *, limit: int = 0) -> Optional[str]:
    """SHA-256 a readable file.  ``None`` if it cannot be read.

    Directories are not hashed (``limit`` is reserved for a future cap).
    """
    del limit
    try:
        if not os.path.isfile(path):
            return None
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def canonical_json_hash(data: Any) -> str:
    """SHA-256 of a stable JSON encoding (sorted keys, no extra whitespace)."""
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_text(encoded)


def git_commit(cwd: Optional[str]) -> Optional[str]:
    """Return ``HEAD`` if ``cwd`` is inside a git work tree; never raise."""
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    commit = (result.stdout or "").strip()
    return commit or None


def snapshot_environment() -> Dict[str, Any]:
    """Record the interpreter and optional extras actually present."""
    extras = {}
    for name, module in _EXTRA_IMPORTS.items():
        extras[name] = _module_available(module)
    # ``models`` is true if either vendor SDK is importable.
    extras["models"] = extras.get("claude", False) or extras.get("gpt", False)
    return {
        "python": _python_version(),
        "analysis_gui": __version__,
        "extras": extras,
    }


def pipeline_environment(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return the optional pin from a pipeline document (``{}`` if absent)."""
    env = data.get("environment")
    if not isinstance(env, dict):
        env = data.get("requires")
    if not isinstance(env, dict):
        return {}
    return env


def environment_is_strict(declared: Dict[str, Any], strict_flag: bool) -> bool:
    """True when mismatches should abort the run."""
    if strict_flag:
        return True
    if declared.get("strict") is True:
        return True
    mismatch = declared.get("on_mismatch")
    return isinstance(mismatch, str) and mismatch.strip().lower() == "error"


def compare_environment(declared: Dict[str, Any], actual: Dict[str, Any]) -> List[str]:
    """Human-readable mismatch messages; empty if nothing is pinned."""
    if not declared:
        return []
    warnings: List[str] = []
    required_py = declared.get("python")
    if isinstance(required_py, str) and required_py.strip():
        if not _version_satisfies(required_py, actual.get("python", "")):
            warnings.append(
                f"python: pipeline pins {required_py!r}, "
                f"interpreter is {actual.get('python')!r}"
            )
    required_pkg = declared.get("analysis_gui")
    if isinstance(required_pkg, str) and required_pkg.strip():
        if not _version_satisfies(required_pkg, actual.get("analysis_gui", "")):
            warnings.append(
                f"analysis_gui: pipeline pins {required_pkg!r}, "
                f"installed is {actual.get('analysis_gui')!r}"
            )
    required_extras = declared.get("extras")
    available = actual.get("extras") or {}
    names: Sequence[str]
    if isinstance(required_extras, str):
        names = [part.strip() for part in required_extras.split(",") if part.strip()]
    elif isinstance(required_extras, (list, tuple)):
        names = [str(item).strip() for item in required_extras if str(item).strip()]
    else:
        names = ()
    for name in names:
        if not available.get(name, False):
            warnings.append(
                f"extra {name!r} is pinned but not importable in this environment"
            )
    return warnings


def collect_input_files(
    nodes: Iterable[Any],
    cwd: str,
) -> List[Dict[str, Any]]:
    """Hash loader ``file_path`` values that resolve to a local file.

    URI resolution (``file://``, http(s), ``s3://``, ``gs://``) is attempted
    when :mod:`analysis_gui.utils.uris` is importable; otherwise only plain
    paths and ``file://`` (via :func:`os.path`) are considered.
    """
    resolve = _resolve_fn()
    seen = set()
    records: List[Dict[str, Any]] = []
    for node in nodes:
        uri = _node_file_path(node)
        if not uri or uri in seen:
            continue
        seen.add(uri)
        resolved = None
        try:
            candidate = resolve(uri, cwd=cwd) if resolve else _plain_join(cwd, uri)
        except Exception:
            candidate = _plain_join(cwd, uri)
        if candidate and os.path.exists(candidate):
            resolved = os.path.abspath(candidate)
        entry: Dict[str, Any] = {"uri": uri, "resolved_path": resolved}
        if resolved and os.path.isfile(resolved):
            digest = sha256_file(resolved)
            if digest:
                entry["sha256"] = digest
        records.append(entry)
    return records


def collect_model_summaries(nodes: Iterable[Any]) -> List[Dict[str, Any]]:
    """Provider / model / truncated prompt for each model-call node."""
    summaries: List[Dict[str, Any]] = []
    for node in nodes:
        node_type = getattr(getattr(node, "node_type", None), "value", None)
        if node_type != "model_call":
            continue
        metadata = getattr(node, "metadata", None) or {}
        prompt = _param(node, "prompt", "")
        if not isinstance(prompt, str):
            prompt = str(prompt)
        summaries.append(
            {
                "node_id": getattr(node, "id", ""),
                "provider": metadata.get("provider"),
                "model": _param(node, "model"),
                "prompt_preview": prompt[:_PROMPT_PREVIEW],
            }
        )
    return summaries


def default_receipt_path(pipeline_path: str) -> str:
    """``<pipeline-stem>.run.json`` beside the pipeline file."""
    root, _ext = os.path.splitext(pipeline_path)
    return root + ".run.json"


def write_receipt(path: str, payload: Dict[str, Any]) -> str:
    """Write ``payload`` as pretty JSON.  Returns the absolute path."""
    absolute = os.path.abspath(path)
    parent = os.path.dirname(absolute)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(absolute, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False, default=str)
        handle.write("\n")
    return absolute


def _python_version() -> str:
    info = sys.version_info
    return f"{info.major}.{info.minor}.{info.micro}"


def _module_available(name: str) -> bool:
    try:
        __import__(name)
    except ImportError:
        return False
    return True


def _version_tuple(text: str) -> Tuple[int, ...]:
    parts: List[int] = []
    for token in text.strip().split("."):
        digits = ""
        for char in token:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or (0,)


def _version_satisfies(required: str, actual: str) -> bool:
    spec = required.strip()
    if spec.startswith(">="):
        return _version_tuple(actual) >= _version_tuple(spec[2:])
    if spec.startswith("=="):
        spec = spec[2:].strip()
    if spec.startswith("="):
        spec = spec.lstrip("=").strip()
    want = _version_tuple(spec)
    have = _version_tuple(actual)
    if len(have) < len(want):
        have = have + (0,) * (len(want) - len(have))
    return have[: len(want)] == want


def _param(node: Any, name: str, default: Any = None) -> Any:
    getter = getattr(node, "get_parameter_value", None)
    if callable(getter):
        return getter(name, default)
    return default


def _node_file_path(node: Any) -> str:
    value = _param(node, "file_path", "")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _plain_join(cwd: str, uri: str) -> str:
    if uri.startswith("file://"):
        from urllib.parse import unquote, urlparse
        from urllib.request import url2pathname

        parsed = urlparse(uri)
        return url2pathname(unquote(parsed.path))
    if os.path.isabs(uri):
        return uri
    return os.path.join(cwd, uri)


def _resolve_fn():
    try:
        from ..utils.uris import resolve_data_uri
    except ImportError:
        return None
    return resolve_data_uri
