"""AST-only scan of Python libraries for analysis-step chunks.

Nothing in this module imports user code.  Files are parsed with
:mod:`ast` so scanning a tree that happens to contain ``import tensorflow``
or ``import mne`` cannot pull those packages in.

Chunks are AST-bounded, not sliding line windows:

* module-level functions
* public class methods
* nested functions with a docstring or an analysis-step name
* inline blocks (``If``/``For``/``With`` bodies, assignment+call runs,
  comment-led groups)
"""

from __future__ import annotations

import ast
import hashlib
import io
import os
import re
import tokenize as tokenize_mod
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

SKIP_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "dist",
        "build",
        "site-packages",
        "tests",
        "test",
    }
)

#: First positional argument names that mean "this function consumes data".
DATA_ARG_NAMES = frozenset(
    {
        "data",
        "df",
        "frame",
        "x",
        "y",
        "signal",
        "signals",
        "recording",
        "recordings",
        "array",
        "arr",
        "series",
        "table",
        "traces",
        "samples",
        "values",
        "waveform",
        "waveforms",
        "times",
        "spikes",
    }
)

#: Names that mean "this is a path, not a data payload".
PATH_ARG_NAMES = frozenset(
    {"path", "file", "filename", "filepath", "file_path", "csv", "source"}
)

TAG_VOCABULARY = (
    "filter",
    "bandpass",
    "lowpass",
    "highpass",
    "notch",
    "psd",
    "spectrum",
    "welch",
    "fft",
    "spike",
    "spikes",
    "isi",
    "psth",
    "raster",
    "eeg",
    "lfp",
    "calcium",
    "neural",
    "load",
    "loader",
    "save",
    "read",
    "write",
    "export",
    "normalize",
    "scale",
    "standardize",
    "cluster",
    "pca",
    "regress",
    "plot",
    "visual",
    "preprocess",
    "analyze",
    "detect",
)

SCI_ROOTS = frozenset(
    {
        "numpy",
        "np",
        "pandas",
        "pd",
        "scipy",
        "sklearn",
        "sk",
        "mne",
        "signal",
        "plt",
        "matplotlib",
        "pyplot",
        "sns",
        "seaborn",
        "neo",
        "spikeinterface",
        "nilearn",
        "nibabel",
    }
)

CHUNK_KINDS = ("function", "method", "block")
COMPOUND_TYPES = (ast.If, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith)

_CAMEL_SPLIT = re.compile(r"(?<!^)(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DUNDER = re.compile(r"^__.*__$")
_SKIP_COMMENT = re.compile(
    r"^(?:!|/|-\*-|coding[:=]|pylint:|noqa|type:\s*ignore|fmt:|isort:|ruff:)",
    re.IGNORECASE,
)

MAX_FILE_BYTES = 1_000_000
MAX_CHUNK_LINES = 80
MIN_CHUNK_LINES = 4
PREVIEW_MAX_LINES = 40
MAX_CHUNKS_PER_FILE = 80
MAX_INDEX_CHUNKS = 20_000
MINIFIED_AVG_LINE_CHARS = 400


@dataclass
class FunctionArg:
    """One parameter of a discovered callable."""

    name: str
    annotation: Optional[str] = None
    default: Any = None
    has_default: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DiscoveredFunction:
    """A Python callable or inline block that looks like an analysis step."""

    name: str
    qualified_name: str
    module: str
    source_path: str
    library_root: str
    lineno: int
    docstring: str = ""
    docstring_first_line: str = ""
    args: List[FunctionArg] = field(default_factory=list)
    return_annotation: Optional[str] = None
    class_name: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    has_data_input: bool = True
    starred: bool = False
    repository_id: Optional[str] = None
    chunk_kind: str = "function"
    end_lineno: int = 0
    preview: str = ""
    source_hash: str = ""
    leading_comment: str = ""
    calls: List[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        """Stable node-kind identifier, ``repo.<module>.<func>``."""
        return f"repo.{self.qualified_name}"

    @property
    def display_name(self) -> str:
        if self.chunk_kind == "block":
            if self.leading_comment:
                return self.leading_comment
            path = Path(self.source_path).name
            end = self.end_lineno or self.lineno
            return f"{path}:{self.lineno}-{end}"
        if self.class_name:
            return f"{self.class_name}.{self.name}"
        return self.name

    @property
    def span(self) -> Dict[str, int]:
        return {"start": self.lineno, "end": self.end_lineno or self.lineno}

    @property
    def document_text(self) -> str:
        """Bag of text used by similar-search."""
        bits = [
            self.name,
            self.name.replace("_", " "),
            self.display_name,
            self.docstring_first_line,
            self.docstring,
            self.leading_comment,
            " ".join(self.tags),
            " ".join(self.calls),
            self.preview,
        ]
        if self.class_name:
            bits.append(self.class_name)
        return " ".join(bit for bit in bits if bit)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind
        payload["display_name"] = self.display_name
        payload["span"] = self.span
        return payload


#: Backwards-compatible alias: every indexed item is a chunk.
DiscoveredChunk = DiscoveredFunction


def default_library_roots(
    workspace: Optional[str] = None,
    extra_roots: Optional[Sequence[str]] = None,
    registered_paths: Optional[Sequence[str]] = None,
    include_config: bool = True,
) -> List[str]:
    """Resolve the directories v1 scans.

    Order: explicit ``extra_roots``, then ``<workspace>/src`` (or the
    workspace itself when there is no ``src/``), then paths listed in
    ``~/.analysis_gui/library_roots.json``, then registered repositories.
    Missing paths are dropped; duplicates are collapsed.
    """
    roots: List[str] = []

    def _add(raw: Optional[str]) -> None:
        if not raw:
            return
        path = os.path.abspath(os.path.expanduser(raw))
        if os.path.isdir(path) and path not in roots:
            roots.append(path)

    if extra_roots:
        for item in extra_roots:
            _add(item)
        # Explicit --root is the whole search set besides registered repos
        # passed in separately; callers that want *only* extra_roots pass
        # registered_paths=() and include_config=False.
        for item in registered_paths or ():
            _add(item)
        return roots

    workspace_path = os.path.abspath(os.path.expanduser(workspace or os.getcwd()))
    src = os.path.join(workspace_path, "src")
    if os.path.isdir(src):
        _add(src)
    else:
        _add(workspace_path)

    if include_config:
        for item in _configured_roots():
            _add(item)

    for item in registered_paths or ():
        _add(item)

    return roots


def _configured_roots() -> List[str]:
    """Optional extra roots from ``~/.analysis_gui/library_roots.json``."""
    import json

    config_path = os.path.expanduser("~/.analysis_gui/library_roots.json")
    if not os.path.isfile(config_path):
        return []
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, str)]
    if isinstance(data, dict) and isinstance(data.get("roots"), list):
        return [item for item in data["roots"] if isinstance(item, str)]
    return []


def scan_python_tree(
    root: str,
    *,
    repository_id: Optional[str] = None,
) -> List[DiscoveredFunction]:
    """Walk ``root`` and return discovered chunks, AST-parse only."""
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        return []

    found: List[DiscoveredFunction] = []
    for file_path in _iter_python_files(root_path):
        if len(found) >= MAX_INDEX_CHUNKS:
            break
        found.extend(
            scan_python_file(
                str(file_path), library_root=str(root_path), repository_id=repository_id
            )
        )
    if len(found) > MAX_INDEX_CHUNKS:
        found = _prioritize_chunks(found)[:MAX_INDEX_CHUNKS]
    return found


def scan_python_file(
    file_path: str,
    *,
    library_root: str,
    repository_id: Optional[str] = None,
) -> List[DiscoveredFunction]:
    """Parse one file with :mod:`ast` and return analysis-step chunks."""
    path = Path(file_path)
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size > MAX_FILE_BYTES:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    if _is_minified(source, size):
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    source_lines = source.splitlines(keepends=True)
    comments = _comments_by_line(source)
    module = module_name_for(path, Path(library_root))
    ctx = _ScanContext(
        module=module,
        source_path=str(path.resolve()),
        library_root=str(Path(library_root).resolve()),
        repository_id=repository_id,
        source_lines=source_lines,
        comments=comments,
    )

    records: List[DiscoveredFunction] = []
    _collect_callables(tree.body, ctx, records, class_name=None, parent_qualname=None)
    records.extend(_blocks_from_stmts(tree.body, ctx, indexed_callables=True))
    return _prioritize_chunks(records)[:MAX_CHUNKS_PER_FILE]


def module_name_for(file_path: Path, root: Path) -> str:
    """Dotted module name of ``file_path`` relative to ``root``."""
    try:
        relative = file_path.resolve().relative_to(root.resolve())
    except ValueError:
        return file_path.stem
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(part for part in parts if part) or file_path.stem


def tokenize(text: str) -> List[str]:
    """Lowercased tokens from a name, docstring or search query."""
    if not text:
        return []
    spaced = _CAMEL_SPLIT.sub(" ", text.replace("_", " "))
    tokens = [tok for tok in _NON_ALNUM.split(spaced.lower()) if tok]
    return tokens


def infer_tags(*texts: str) -> List[str]:
    """Tags drawn from ``TAG_VOCABULARY`` that appear in the given texts."""
    tokens = set()
    for text in texts:
        tokens.update(tokenize(text))
    tags = [tag for tag in TAG_VOCABULARY if tag in tokens]
    return tags


def count_chunks_by_kind(records: Sequence[DiscoveredFunction]) -> Dict[str, int]:
    """Return ``{function, method, block}`` counts, always with all keys."""
    counts = Counter(record.chunk_kind for record in records)
    return {kind: int(counts.get(kind, 0)) for kind in CHUNK_KINDS}


def _iter_python_files(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in SKIP_DIR_NAMES and not name.startswith(".")
        ]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            if name.startswith("test_") or name.endswith("_test.py"):
                continue
            yield Path(dirpath) / name


@dataclass
class _ScanContext:
    module: str
    source_path: str
    library_root: str
    repository_id: Optional[str]
    source_lines: Sequence[str]
    comments: Dict[int, str]


def _collect_callables(
    stmts: Sequence[ast.stmt],
    ctx: _ScanContext,
    records: List[DiscoveredFunction],
    *,
    class_name: Optional[str],
    parent_qualname: Optional[str],
) -> None:
    for node in stmts:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            record = _function_from_ast(
                node,
                ctx=ctx,
                class_name=class_name,
                parent_qualname=parent_qualname,
                nested=parent_qualname is not None and class_name is None,
            )
            child_parent = (
                record.qualified_name
                if record is not None
                else ".".join(
                    part
                    for part in (parent_qualname or ctx.module, class_name, node.name)
                    if part
                )
            )
            if record is not None:
                records.append(record)
            _collect_callables(
                node.body,
                ctx,
                records,
                class_name=None,
                parent_qualname=child_parent,
            )
            if record is None:
                records.extend(
                    _blocks_from_stmts(node.body, ctx, indexed_callables=False)
                )
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _collect_callables(
                        [item],
                        ctx,
                        records,
                        class_name=node.name,
                        parent_qualname=parent_qualname,
                    )


def _function_from_ast(
    node: ast.FunctionDef,
    *,
    ctx: _ScanContext,
    class_name: Optional[str],
    parent_qualname: Optional[str],
    nested: bool,
) -> Optional[DiscoveredFunction]:
    if _DUNDER.match(node.name):
        return None

    docstring = ast.get_docstring(node) or ""
    first_line = docstring.strip().splitlines()[0].strip() if docstring.strip() else ""

    # Private helpers stay hidden unless the author documented them.
    if node.name.startswith("_") and not docstring:
        return None

    args = _args_from_ast(node, skip_receiver=class_name is not None)
    has_annotation = node.returns is not None or any(arg.annotation for arg in args)
    if nested:
        if not docstring and not _looks_like_analysis_step(node.name):
            return None
    elif not docstring and not has_annotation:
        return None

    if parent_qualname and nested:
        qualified = f"{parent_qualname}.{node.name}"
    else:
        qualified = ".".join(
            part for part in (ctx.module, class_name, node.name) if part
        )

    start = getattr(node, "lineno", 0) or 0
    end = _end_lineno(node)
    source = _source_span(ctx.source_lines, start, end)
    calls = _call_names(node)
    tags = infer_tags(
        node.name, class_name or "", first_line, docstring, " ".join(calls)
    )
    starred = bool(docstring) and has_annotation and bool(tags)
    chunk_kind = "method" if class_name and not nested else "function"

    return DiscoveredFunction(
        name=node.name,
        qualified_name=qualified,
        module=ctx.module,
        source_path=ctx.source_path,
        library_root=ctx.library_root,
        lineno=start,
        end_lineno=end,
        docstring=docstring,
        docstring_first_line=first_line,
        args=args,
        return_annotation=_ann_str(node.returns),
        class_name=class_name if not nested else None,
        tags=tags,
        has_data_input=_has_data_input(args),
        starred=starred,
        repository_id=ctx.repository_id,
        chunk_kind=chunk_kind,
        preview=_preview(source),
        source_hash=_hash_text(source),
        calls=calls,
    )


def _blocks_from_stmts(
    stmts: Sequence[ast.stmt],
    ctx: _ScanContext,
    *,
    indexed_callables: bool,
) -> List[DiscoveredFunction]:
    """Extract inline blocks from a statement list.

    When ``indexed_callables`` is true, function/class defs in ``stmts`` are
    already indexed (or walked) and their bodies are not re-emitted as blocks.
    """
    records: List[DiscoveredFunction] = []
    occupied: List[Tuple[int, int]] = []

    if indexed_callables:
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                occupied.append((stmt.lineno, _end_lineno(stmt)))

    comment_used_until = 0
    for index, stmt in enumerate(stmts):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if _is_ignored_stmt(stmt):
            continue
        if stmt.lineno <= comment_used_until:
            continue
        leading_line, leading_text = _leading_comment_for(stmts, index, ctx)
        if not leading_text:
            continue
        group = _consume_step_run(stmts, index, ctx, occupied)
        if not group:
            continue
        record = _block_from_nodes(
            group, ctx, leading_comment=leading_text, start_override=leading_line
        )
        if record is not None:
            records.append(record)
            occupied.append((record.lineno, record.end_lineno))
            comment_used_until = record.end_lineno

    for stmt in stmts:
        if not isinstance(stmt, COMPOUND_TYPES):
            continue
        if _range_occupied(occupied, stmt.lineno, _end_lineno(stmt)):
            continue
        n_lines = _end_lineno(stmt) - stmt.lineno + 1
        if n_lines > MAX_CHUNK_LINES:
            for body in _compound_bodies(stmt):
                records.extend(_blocks_from_stmts(body, ctx, indexed_callables=False))
            continue
        record = _block_from_nodes([stmt], ctx, leading_comment="")
        if record is not None:
            records.append(record)
            occupied.append((record.lineno, record.end_lineno))

    run: List[ast.stmt] = []
    for stmt in stmts:
        if _range_occupied(occupied, stmt.lineno, _end_lineno(stmt)):
            _flush_run(run, ctx, records, occupied)
            run = []
            continue
        if not _is_step_stmt(stmt):
            _flush_run(run, ctx, records, occupied)
            run = []
            continue
        if run and _blank_separated(run[-1], stmt, ctx.source_lines):
            _flush_run(run, ctx, records, occupied)
            run = [stmt]
            continue
        run.append(stmt)
    _flush_run(run, ctx, records, occupied)
    return records


def _flush_run(
    run: List[ast.stmt],
    ctx: _ScanContext,
    records: List[DiscoveredFunction],
    occupied: List[Tuple[int, int]],
) -> None:
    if not run:
        return
    record = _block_from_nodes(run, ctx, leading_comment="")
    if record is not None:
        records.append(record)
        occupied.append((record.lineno, record.end_lineno))


def _consume_step_run(
    stmts: Sequence[ast.stmt],
    start_index: int,
    ctx: _ScanContext,
    occupied: Sequence[Tuple[int, int]],
) -> List[ast.stmt]:
    group: List[ast.stmt] = []
    for stmt in stmts[start_index:]:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            break
        if _is_ignored_stmt(stmt):
            if group:
                break
            continue
        if _range_occupied(occupied, stmt.lineno, _end_lineno(stmt)):
            break
        if group and _blank_separated(group[-1], stmt, ctx.source_lines):
            break
        if group:
            next_comment_line, next_comment = _leading_comment_between(
                group[-1], stmt, ctx
            )
            if next_comment and next_comment_line > group[-1].lineno:
                break
        if not (_is_step_stmt(stmt) or isinstance(stmt, COMPOUND_TYPES)):
            if group:
                break
            continue
        group.append(stmt)
        n_lines = _end_lineno(group[-1]) - group[0].lineno + 1
        if n_lines >= MAX_CHUNK_LINES:
            break
    return group


def _block_from_nodes(
    nodes: Sequence[ast.stmt],
    ctx: _ScanContext,
    *,
    leading_comment: str,
    start_override: int = 0,
) -> Optional[DiscoveredFunction]:
    if not nodes:
        return None
    start = start_override or nodes[0].lineno
    end = _end_lineno(nodes[-1])
    n_lines = end - start + 1
    if n_lines > MAX_CHUNK_LINES:
        return None
    if all(_is_ignored_stmt(node) for node in nodes):
        return None

    is_compound = len(nodes) == 1 and isinstance(nodes[0], COMPOUND_TYPES)
    has_sci = any(_has_sci_call(node) for node in nodes)
    has_call = any(_has_call(node) for node in nodes)

    if not _block_is_valid(
        nodes,
        n_lines=n_lines,
        leading_comment=leading_comment,
        has_sci=has_sci,
        has_call=has_call,
        is_compound=is_compound,
    ):
        return None

    source = _source_span(ctx.source_lines, start, end)
    if not source.strip():
        return None
    source_hash = _hash_text(source)
    calls = []
    for node in nodes:
        calls.extend(_call_names(node))
    # Preserve order, drop duplicates.
    seen = set()
    unique_calls: List[str] = []
    for call in calls:
        if call and call not in seen:
            seen.add(call)
            unique_calls.append(call)

    identifiers = []
    for node in nodes:
        identifiers.extend(_identifier_names(node))
    first_line = (
        leading_comment.strip().splitlines()[0].strip() if leading_comment else ""
    )
    tags = infer_tags(
        first_line,
        leading_comment,
        " ".join(unique_calls),
        " ".join(identifiers),
        _preview(source),
    )
    helper_name = f"chunk_{source_hash}"
    qualified = f"{ctx.module}:{start}-{end}" if ctx.module else f"{start}-{end}"

    return DiscoveredFunction(
        name=helper_name,
        qualified_name=qualified,
        module=ctx.module,
        source_path=ctx.source_path,
        library_root=ctx.library_root,
        lineno=start,
        end_lineno=end,
        docstring=leading_comment,
        docstring_first_line=first_line,
        args=[FunctionArg(name="data")],
        class_name=None,
        tags=tags,
        has_data_input=True,
        starred=False,
        repository_id=ctx.repository_id,
        chunk_kind="block",
        preview=_preview(source),
        source_hash=source_hash,
        leading_comment=leading_comment,
        calls=unique_calls,
    )


def _block_is_valid(
    nodes: Sequence[ast.stmt],
    *,
    n_lines: int,
    leading_comment: str,
    has_sci: bool,
    has_call: bool,
    is_compound: bool,
) -> bool:
    if n_lines < 1:
        return False
    if leading_comment:
        comment_tags = infer_tags(leading_comment)
        if comment_tags or has_sci or n_lines >= MIN_CHUNK_LINES:
            return has_call or bool(comment_tags) or n_lines >= MIN_CHUNK_LINES
        return False
    if is_compound:
        body_stmts = []
        for node in nodes:
            for body in _compound_bodies(node):
                body_stmts.extend(body)
        if not body_stmts or all(_is_ignored_stmt(stmt) for stmt in body_stmts):
            return False
        return n_lines >= MIN_CHUNK_LINES or has_sci
    if len(nodes) < 2:
        return False
    if not has_call:
        return False
    return n_lines >= MIN_CHUNK_LINES or has_sci


def _args_from_ast(node: ast.FunctionDef, *, skip_receiver: bool) -> List[FunctionArg]:
    positional = list(node.args.args)
    if skip_receiver and positional and positional[0].arg in ("self", "cls"):
        positional = positional[1:]

    defaults = list(node.args.defaults)
    missing = len(positional) - len(defaults)
    default_for: List[Optional[ast.AST]] = [None] * max(missing, 0) + list(defaults)

    records: List[FunctionArg] = []
    for arg, default_node in zip(positional, default_for):
        has_default = default_node is not None
        records.append(
            FunctionArg(
                name=arg.arg,
                annotation=_ann_str(arg.annotation),
                default=_literal(default_node) if has_default else None,
                has_default=has_default,
            )
        )
    return records


def _has_data_input(args: Sequence[FunctionArg]) -> bool:
    if not args:
        return False
    first = args[0]
    name = first.name.lower()
    if name in PATH_ARG_NAMES:
        return False
    if name in DATA_ARG_NAMES:
        return True
    annotation = (first.annotation or "").lower()
    if any(
        needle in annotation
        for needle in ("dataframe", "ndarray", "ndarray", "series", "signal", "array")
    ):
        return True
    # Default: a first argument that is not clearly a path is data-like.
    return name not in {"path", "file", "filename", "name", "key"}


def _ann_str(node: Optional[ast.AST]) -> Optional[str]:
    if node is None:
        return None
    if hasattr(ast, "unparse"):
        try:
            return ast.unparse(node)
        except Exception:
            pass
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _ann_str(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Constant):
        return repr(node.value)
    return None


def _literal(node: Optional[ast.AST]) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        text = _ann_str(node)
        return text


def merge_discoveries(
    groups: Iterable[Iterable[DiscoveredFunction]],
) -> List[DiscoveredFunction]:
    """Concatenate discoveries, last write wins on ``kind`` collisions."""
    by_kind: Dict[str, DiscoveredFunction] = {}
    for group in groups:
        for record in group:
            by_kind[record.kind] = record
    return list(by_kind.values())


def _looks_like_analysis_step(name: str) -> bool:
    return bool(infer_tags(name))


def _is_minified(source: str, size: int) -> bool:
    lines = source.splitlines()
    if not lines:
        return True
    if len(lines) <= 2 and size > 8_000:
        return True
    avg = size / max(len(lines), 1)
    return avg > MINIFIED_AVG_LINE_CHARS and len(lines) < 30


def _comments_by_line(source: str) -> Dict[int, str]:
    comments: Dict[int, str] = {}
    lines = source.splitlines()
    try:
        tokens = tokenize_mod.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type != tokenize_mod.COMMENT:
                continue
            text = tok.string.lstrip("#").strip()
            if not text or _SKIP_COMMENT.match(text):
                continue
            # Ignore end-of-line comments; only whole-line comments lead groups.
            line_index = tok.start[0] - 1
            line_start = lines[line_index] if 0 <= line_index < len(lines) else ""
            prefix = line_start[: max(tok.start[1], 0)].strip()
            if prefix:
                continue
            comments[tok.start[0]] = text
    except (tokenize_mod.TokenError, IndentationError, SyntaxError):
        return comments
    return comments


def _leading_comment_for(
    stmts: Sequence[ast.stmt], index: int, ctx: _ScanContext
) -> Tuple[int, str]:
    stmt = stmts[index]
    prev_end = 0
    if index > 0:
        prev_end = _end_lineno(stmts[index - 1])
    return _comments_between(prev_end + 1, stmt.lineno - 1, ctx.comments)


def _leading_comment_between(
    prev: ast.stmt, nxt: ast.stmt, ctx: _ScanContext
) -> Tuple[int, str]:
    return _comments_between(_end_lineno(prev) + 1, nxt.lineno - 1, ctx.comments)


def _comments_between(
    start_line: int, end_line: int, comments: Dict[int, str]
) -> Tuple[int, str]:
    if end_line < start_line:
        return 0, ""
    lines = sorted(line for line in comments if start_line <= line <= end_line)
    if not lines:
        return 0, ""
    text = " ".join(comments[line] for line in lines)
    return lines[0], text


def _is_ignored_stmt(node: ast.stmt) -> bool:
    if isinstance(node, (ast.Import, ast.ImportFrom, ast.Pass)):
        return True
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return True
    return False


def _is_step_stmt(node: ast.stmt) -> bool:
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return True
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        return True
    return False


def _has_call(node: ast.AST) -> bool:
    return any(isinstance(child, ast.Call) for child in ast.walk(node))


def _has_sci_call(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        root = _call_root(child.func)
        if root in SCI_ROOTS:
            return True
        qual = _call_qualname(child.func)
        if any(part in SCI_ROOTS for part in qual.split(".")):
            return True
    return False


def _call_names(node: ast.AST) -> List[str]:
    names: List[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            qual = _call_qualname(child.func)
            if qual:
                names.append(qual)
    return names


def _call_qualname(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parent = _call_qualname(func.value)
        return f"{parent}.{func.attr}" if parent else func.attr
    return ""


def _call_root(func: ast.AST) -> str:
    current = func
    while isinstance(current, ast.Attribute):
        current = current.value
    if isinstance(current, ast.Name):
        return current.id
    return ""


def _identifier_names(node: ast.AST) -> List[str]:
    return [
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    ]


def _compound_bodies(node: ast.stmt) -> List[List[ast.stmt]]:
    bodies: List[List[ast.stmt]] = []
    for attr in ("body", "orelse", "finalbody", "handlers"):
        value = getattr(node, attr, None)
        if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
            bodies.append(value)
        elif attr == "handlers" and value:
            for handler in value:
                if getattr(handler, "body", None):
                    bodies.append(handler.body)
    return bodies


def _end_lineno(node: ast.AST) -> int:
    end = getattr(node, "end_lineno", None)
    if isinstance(end, int) and end > 0:
        return end
    last = getattr(node, "lineno", 0) or 0
    for child in ast.walk(node):
        last = max(
            last,
            getattr(child, "lineno", 0) or 0,
            getattr(child, "end_lineno", 0) or 0,
        )
    return last


def _source_span(source_lines: Sequence[str], start: int, end: int) -> str:
    if start < 1:
        start = 1
    if end < start:
        end = start
    return "".join(source_lines[start - 1 : end])


def _preview(text: str, max_lines: int = PREVIEW_MAX_LINES) -> str:
    lines = text.splitlines()
    return "\n".join(lines[:max_lines])


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _range_occupied(occupied: Sequence[Tuple[int, int]], start: int, end: int) -> bool:
    for occ_start, occ_end in occupied:
        latest_start = max(occ_start, start)
        earliest_end = min(occ_end, end)
        if latest_start <= earliest_end:
            overlap = earliest_end - latest_start + 1
            span = max(end - start + 1, 1)
            if overlap / span >= 0.5:
                return True
    return False


def _blank_separated(
    prev: ast.stmt, nxt: ast.stmt, source_lines: Sequence[str]
) -> bool:
    prev_end = _end_lineno(prev)
    for line_no in range(prev_end + 1, nxt.lineno):
        index = line_no - 1
        if 0 <= index < len(source_lines) and not source_lines[index].strip():
            return True
    return False


def _prioritize_chunks(
    records: Sequence[DiscoveredFunction],
) -> List[DiscoveredFunction]:
    order = {"function": 0, "method": 1, "block": 2}
    return sorted(
        records,
        key=lambda record: (
            order.get(record.chunk_kind, 9),
            record.source_path,
            record.lineno,
            record.qualified_name,
        ),
    )
