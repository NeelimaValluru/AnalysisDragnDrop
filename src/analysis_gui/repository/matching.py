"""ApiIntentMatch: dual-view retrieval of scientific pipeline fragments.

This module ranks discovered library chunks for ``similar``.  The *combination*
is the product claim; the pieces are classic IR and clone detection.

**What is actually novel here (and what is not).**  Clone detectors (MOSS,
SourcererCC) typically *discard* comments and match token/k-gram fingerprints
of code.  Lab scientists, conversely, explain *what* a step does in comments
and docstrings, while the *how* is a short sequence of library calls
(``signal.butter`` then ``signal.sosfilt``) whose local variable names are
noise.  ApiIntentMatch keeps both views, then adds a cheap neural/data-kind
prior so an EEG query does not rank a spike-raster chunk first.  Sequence
alignment scores call *order* (butter→sosfilt→plot beats a lone ``butter``).
Same-file wrapper expansion inlines one to two hops of local helpers so a
``self.filter()`` wrapper still matches ``bandpass``.  Relative imports in
the same package (``from .filters import bandpass``, depth 1–2) inherit that
callee's API sequence.  There is no learned model, no CFG, no absolute-import
resolution, and no Type-4 semantic clone detector.

Views, built once per chunk at index time:

1. **Intent view** — docstring, leading comments, function/method name,
   ``#`` comments inside the span, and assigned identifier names (scientists
   often name the *what* — ``filtered_eeg``, ``uncommented_bandpass``).
   Camel/snake split, lowercased, stopwords dropped.  Callee names belong
   to the behavior view, not this bag.
2. **Behavior view** — ordered, alias-normalized callee names walked from the
   chunk AST (``numpy.mean`` → ``np.mean``).  Same-file / same-module wrappers
   inherit the callee's APIs (depth ≤ 2; AST only).  One-hop relative imports
   (``from .mod import name``, same package) inherit that name's sequence.  k-grams
   with k=2 and k=3, plus unigrams when the sequence is short.  MinHash (64
   permutations) approximates k-gram Jaccard; winnowing fingerprints
   (Schleimer et al., like MOSS) feed a second posting list.
3. **Kind prior** — ``eeg | spike | lfp | calcium | table`` inferred from
   names, comments, imports and existing tags.

**Index.**  Inverted lists ``intent term → chunk ids`` and
``api k-gram / winnow hash → chunk ids``.  Query candidate generation is the
union of those lists (sublinear in corpus size when terms are selective).
If the union is empty, fall back to a full corpus scan so recall does not
collapse.  Smith–Waterman alignment runs **only** on inverted-index
candidates, never the full corpus.

**Score (defaults; tunable constants below).**

    intent = 0.55 * query_term_coverage + 0.45 * tfidf_cosine
    api    = 0.60 * leaf_f1(callees) + 0.40 * minhash_jaccard
    align  = smith_waterman(Q_seq, D_seq) / (match · |Q_seq|)
    kind   = |Q_kind ∩ D_kind| / |Q_kind|   (0 if the query has no kind)
    score  = α * intent + β * api + γ * kind + δ * align
             α=0.34, β=0.33, γ=0.13, δ=0.20

``leaf_f1`` is the F1 of callee *leaf* names (``butter`` in ``signal.butter``),
so ``numpy.mean`` and ``np.mean`` still overlap after alias normalization.
Alignment uses a small substitution table (``np.mean``~``numpy.mean``,
``bandpass_filter``~``signal.butter``).  Query expansion and that table live
in :mod:`analysis_gui.repository.lexicon`.

Optional OpenAI embeddings, when enabled by the caller, rerank the top 50
of *this* candidate set; they do not replace it.

**Query-by-chunk.**  ``similar --from-span path.py:12-40`` and
``--from-kind repo.module.func`` use that chunk's intent+api+kind as the
query (find code like this block).

**Complexity.**  Index build is O(N · (L + P + W)) for N chunks, L
tokens/calls per chunk, P MinHash permutations (P=64), W local defs per
file (wrapper expansion, depth ≤ 2).  Query candidate generation is
O(|Q| · mean posting-list length); scoring is O(|C| · (F + S)) for C
candidates, F bag features, and S = |Q_seq| · |D_seq| alignment.  C ≪ N
on selective queries.  No new required dependencies.
"""

from __future__ import annotations

import ast
import hashlib
import io
import math
import os
import re
import textwrap
import tokenize as tokenize_mod
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import (
    Any,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from .lexicon import (
    API_EQUIV_GROUPS,
    KIND_HINTS,
    OPERATION_APIS,
    OPERATION_PHRASES,
)
from .scan import DiscoveredFunction, tokenize

_SKIP_COMMENT = re.compile(
    r"^(?:!|/|-\*-|coding[:=]|pylint:|noqa|type:\s*ignore|fmt:|isort:|ruff:)",
    re.IGNORECASE,
)

RANKER_NAME = "ApiIntentMatch"
RANKER_API_INTENT = "api_intent"
RANKER_LEGACY = "legacy_tfidf"

# score = α * intent + β * api + γ * kind + δ * align
ALPHA = 0.34
BETA = 0.33
GAMMA = 0.13
DELTA = 0.20
INTENT_COVERAGE_WEIGHT = 0.55
INTENT_TFIDF_WEIGHT = 0.45
API_JACCARD_WEIGHT = 0.60
API_MINHASH_WEIGHT = 0.40
STAR_BONUS = 0.02

MINHASH_PERMUTATIONS = 64
WINNOW_WINDOW = 4
KGRAM_K = (2, 3)
WRAPPER_EXPAND_DEPTH = 2

ALIGN_MATCH = 2.0
ALIGN_SUBST = 1.5
ALIGN_MISMATCH = -1.0
ALIGN_GAP = -1.0

STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "on",
        "with",
        "this",
        "that",
        "is",
        "are",
        "be",
        "as",
        "from",
        "by",
        "at",
        "it",
        "its",
        "if",
        "else",
        "not",
        "no",
        "yes",
        "return",
        "returns",
        "returned",
        "none",
        "true",
        "false",
        "self",
        "cls",
        "def",
        "class",
        "import",
        "into",
        "using",
        "use",
        "used",
        "between",
        "over",
        "per",
        "via",
        "than",
        "then",
        "them",
        "their",
        "can",
        "will",
        "chunk",
    }
)

DATA_KINDS = ("eeg", "spike", "lfp", "calcium", "table")

_SELF_NAMES = frozenset({"self", "cls"})
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*", re.IGNORECASE)
_SPAN_TAIL = re.compile(r":(\d+)-(\d+)$")

# Canonical short prefixes used in scientific Python.
_MODULE_CANON = {
    "numpy": "np",
    "pandas": "pd",
    "seaborn": "sns",
    "pyplot": "plt",
}

_EXACT_MODULE = {
    "scipy.signal": "signal",
    "matplotlib.pyplot": "plt",
    "numpy": "np",
    "pandas": "pd",
    "seaborn": "sns",
}

_DOTTED_PREFIX_CANON = (
    ("matplotlib.pyplot.", "plt."),
    ("scipy.signal.", "signal."),
    ("numpy.", "np."),
    ("pandas.", "pd."),
)

_KNOWN_MODULE_TAILS = frozenset(
    {
        "numpy",
        "np",
        "pandas",
        "pd",
        "scipy",
        "signal",
        "pyplot",
        "plt",
        "matplotlib",
        "seaborn",
        "sns",
        "sklearn",
        "mne",
        "fft",
        "linalg",
        "optimize",
        "stats",
        "interpolate",
        "ndimage",
    }
)

_DOTTED_NAME = re.compile(r"\b[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+")
_BLOCK_NAME = re.compile(r"^chunk_[0-9a-f]+$")
_CODE_LIKE = re.compile(r"[(\n]|[a-zA-Z_][a-zA-Z0-9_]*\s*\(")


@dataclass(frozen=True)
class ScoreBreakdown:
    """Per-view scores plus the fused total."""

    intent: float
    api: float
    kind: float
    align: float
    total: float

    def to_dict(self) -> Dict[str, float]:
        return {
            "intent": round(self.intent, 6),
            "api": round(self.api, 6),
            "kind": round(self.kind, 6),
            "align": round(self.align, 6),
            "total": round(self.total, 6),
        }


@dataclass
class ScoredHit:
    record: DiscoveredFunction
    breakdown: ScoreBreakdown


@dataclass
class QueryStats:
    candidates_examined: int
    corpus_size: int
    used_fallback: bool
    alignments_scored: int = 0
    ranker: str = RANKER_NAME


@dataclass
class QueryResult:
    hits: List[ScoredHit]
    stats: QueryStats


@dataclass
class QueryViews:
    """Intent / API / kind extracted from a text query or a source chunk."""

    intent: Tuple[str, ...]
    api_seq: Tuple[str, ...]
    api_set: FrozenSet[str]
    kinds: FrozenSet[str]
    text: str = ""
    exclude_key: Optional[Tuple[str, int, int]] = None


@dataclass
class _FileAliases:
    """Import aliases for one source file."""

    prefixes: Dict[str, str] = field(default_factory=dict)
    names: Dict[str, str] = field(default_factory=dict)


@dataclass
class _LocalDef:
    """One same-file function or method used for wrapper expansion."""

    name: str
    api_seq: Tuple[str, ...]
    local_callees: Tuple[str, ...]


@dataclass
class _ChunkFeatures:
    record: DiscoveredFunction
    intent_tokens: Tuple[str, ...]
    intent_set: FrozenSet[str]
    api_seq: Tuple[str, ...]
    api_set: FrozenSet[str]
    api_grams: FrozenSet[str]
    minhash: Tuple[int, ...]
    winnow: FrozenSet[int]
    kind_tags: FrozenSet[str]


class ApiIntentIndex:
    """In-memory dual-view index over discovered chunks."""

    def __init__(self, features: Sequence[_ChunkFeatures]):
        self.features: List[_ChunkFeatures] = list(features)
        self.n_docs = len(self.features)
        self.intent_postings: Dict[str, List[int]] = defaultdict(list)
        self.api_postings: Dict[str, List[int]] = defaultdict(list)
        self.winnow_postings: Dict[int, List[int]] = defaultdict(list)
        self.intent_df: Counter[str] = Counter()
        self.leaf_to_qualified: Dict[str, str] = {}
        self.known_leaves: Set[str] = set()
        self._build_postings()

    @classmethod
    def build(cls, records: Sequence[DiscoveredFunction]) -> "ApiIntentIndex":
        aliases_by_path = _aliases_for_records(records)
        helpers_by_path = _local_defs_for_records(records, aliases_by_path)
        _inherit_relative_import_apis(helpers_by_path)
        features = [
            _features_for(
                record,
                aliases_by_path.get(record.source_path, _FileAliases()),
                helpers_by_path.get(record.source_path, {}),
            )
            for record in records
        ]
        return cls(features)

    def _build_postings(self) -> None:
        leaf_counts: Counter[str] = Counter()
        leaf_example: Dict[str, str] = {}
        for index, feat in enumerate(self.features):
            self.intent_df.update(feat.intent_set)
            for term in feat.intent_set:
                self.intent_postings[term].append(index)
            for gram in feat.api_grams:
                self.api_postings[gram].append(index)
            for fingerprint in feat.winnow:
                self.winnow_postings[fingerprint].append(index)
            for callee in feat.api_seq:
                leaf = callee.split(".")[-1]
                if leaf:
                    self.known_leaves.add(leaf)
                    leaf_counts[leaf] += 1
                    leaf_example.setdefault(leaf, callee)
        for leaf, _count in leaf_counts.most_common():
            self.leaf_to_qualified[leaf] = leaf_example[leaf]

    def query(
        self,
        query: str = "",
        *,
        from_span: Optional[str] = None,
        from_kind: Optional[str] = None,
        fallback_records: Optional[Sequence[DiscoveredFunction]] = None,
        fallback_ranker=None,
    ) -> QueryResult:
        """Rank chunks for a text query, ``--from-span``, or ``--from-kind``.

        ``fallback_ranker``, if given, is ``(query, records) -> [(score, record)]``
        and is used only when posting-list union is empty.
        """
        stats = QueryStats(
            candidates_examined=0,
            corpus_size=self.n_docs,
            used_fallback=False,
        )
        records = (
            fallback_records
            if fallback_records is not None
            else [feat.record for feat in self.features]
        )
        if not self.features:
            return QueryResult(hits=[], stats=stats)

        try:
            views = self._views_for(
                query, from_span=from_span, from_kind=from_kind, records=records
            )
        except ValueError:
            return QueryResult(hits=[], stats=stats)
        if views is None:
            return QueryResult(hits=[], stats=stats)

        return self._query_views(
            views,
            stats=stats,
            records=records,
            fallback_ranker=fallback_ranker,
        )

    def _views_for(
        self,
        query: str,
        *,
        from_span: Optional[str],
        from_kind: Optional[str],
        records: Sequence[DiscoveredFunction],
    ) -> Optional[QueryViews]:
        if from_span:
            return self._views_from_span(from_span, records)
        if from_kind:
            return self._views_from_kind(from_kind, records)
        if not query or not query.strip():
            return None
        return self._views_from_text(query)

    def _views_from_text(self, query: str) -> QueryViews:
        intent = tuple(_intent_tokens_from_text(query))
        kinds = infer_data_kinds(query)
        api_seq, api_set = self._query_apis(query, intent)
        return QueryViews(
            intent=intent, api_seq=api_seq, api_set=api_set, kinds=kinds, text=query
        )

    def _views_from_feat(
        self, feat: _ChunkFeatures, *, exclude: bool = True
    ) -> QueryViews:
        record = feat.record
        exclude_key = (
            (record.source_path, record.lineno, record.end_lineno or record.lineno)
            if exclude
            else None
        )
        return QueryViews(
            intent=feat.intent_tokens,
            api_seq=feat.api_seq,
            api_set=feat.api_set,
            kinds=feat.kind_tags,
            text=record.document_text,
            exclude_key=exclude_key,
        )

    def _views_from_span(
        self, spec: str, records: Sequence[DiscoveredFunction]
    ) -> Optional[QueryViews]:
        path, start, end = parse_span_spec(spec)
        record = find_record_for_span(records, path, start, end)
        if record is not None:
            feat = self._feature_for_record(record)
            if feat is not None:
                return self._views_from_feat(feat)
            return _views_from_record(record)
        if os.path.isfile(path):
            return _views_from_file_span(path, start, end)
        return None

    def _views_from_kind(
        self, kind: str, records: Sequence[DiscoveredFunction]
    ) -> Optional[QueryViews]:
        record = find_record_for_kind(records, kind)
        if record is None:
            return None
        feat = self._feature_for_record(record)
        if feat is not None:
            return self._views_from_feat(feat)
        return _views_from_record(record)

    def _feature_for_record(
        self, record: DiscoveredFunction
    ) -> Optional[_ChunkFeatures]:
        key = (record.source_path, record.lineno, record.end_lineno or record.lineno)
        for feat in self.features:
            rec = feat.record
            if (
                rec.source_path,
                rec.lineno,
                rec.end_lineno or rec.lineno,
            ) == key:
                return feat
            if (
                rec.qualified_name == record.qualified_name
                and rec.source_path == record.source_path
            ):
                return feat
        return None

    def _query_views(
        self,
        views: QueryViews,
        *,
        stats: QueryStats,
        records: Sequence[DiscoveredFunction],
        fallback_ranker=None,
    ) -> QueryResult:
        q_grams = _api_grams(
            views.api_seq if views.api_seq else tuple(sorted(views.api_set))
        )
        q_winnow = _winnow_fingerprints(q_grams)
        candidate_ids = self._candidate_ids(views.intent, q_grams, q_winnow)
        if not candidate_ids:
            if fallback_ranker is not None:
                legacy = fallback_ranker(views.text or " ", records)
                stats.used_fallback = True
                stats.candidates_examined = len(records)
                stats.alignments_scored = 0
                hits = [
                    ScoredHit(
                        record=record,
                        breakdown=ScoreBreakdown(
                            intent=float(score),
                            api=0.0,
                            kind=0.0,
                            align=0.0,
                            total=float(score),
                        ),
                    )
                    for score, record in legacy
                    if score > 0
                ]
                hits = _exclude_query_chunk(hits, views.exclude_key)
                return QueryResult(hits=hits, stats=stats)
            candidate_ids = set(range(self.n_docs))
            stats.used_fallback = True

        run_align = not stats.used_fallback
        stats.candidates_examined = len(candidate_ids)
        stats.alignments_scored = len(candidate_ids) if run_align else 0
        query_tf = Counter(views.intent)
        query_minhash = _minhash(q_grams)
        hits: List[ScoredHit] = []
        for index in candidate_ids:
            feat = self.features[index]
            if _same_span(feat.record, views.exclude_key):
                continue
            breakdown = _score_pair(
                query_tokens=views.intent,
                query_tf=query_tf,
                query_api=views.api_set,
                query_seq=views.api_seq,
                query_grams=q_grams,
                query_minhash=query_minhash,
                query_kinds=views.kinds,
                feat=feat,
                intent_df=self.intent_df,
                n_docs=self.n_docs,
                run_align=run_align,
            )
            if breakdown.total > 0:
                hits.append(ScoredHit(record=feat.record, breakdown=breakdown))

        hits.sort(key=lambda hit: (-hit.breakdown.total, hit.record.qualified_name))
        return QueryResult(hits=hits, stats=stats)

    def _candidate_ids(
        self,
        intent_terms: Sequence[str],
        api_grams: Iterable[str],
        winnow: Iterable[int],
    ) -> Set[int]:
        ids: Set[int] = set()
        for term in set(intent_terms):
            posting = self.intent_postings.get(term)
            if posting:
                ids.update(posting)
        for gram in api_grams:
            posting = self.api_postings.get(gram)
            if posting:
                ids.update(posting)
        for fingerprint in winnow:
            posting = self.winnow_postings.get(fingerprint)
            if posting:
                ids.update(posting)
        return ids

    def _query_apis(
        self, query: str, intent_terms: Sequence[str]
    ) -> Tuple[Tuple[str, ...], FrozenSet[str]]:
        ordered: List[str] = []
        seen: Set[str] = set()

        def _add(name: str) -> None:
            canon = canonicalize_call(name, _FileAliases())
            if canon and canon not in seen:
                seen.add(canon)
                ordered.append(canon)

        if _looks_like_code(query):
            for callee in _api_seq_from_source(query, _FileAliases()):
                _add(callee)

        for match in _DOTTED_NAME.findall(query.lower()):
            _add(match)

        for term in intent_terms:
            if term in OPERATION_APIS:
                for callee in OPERATION_APIS[term]:
                    _add(callee)
            if term in self.leaf_to_qualified:
                _add(self.leaf_to_qualified[term])
            if term in self.known_leaves:
                _add(self.leaf_to_qualified.get(term, term))

        lowered = query.lower()
        for phrase, callees in OPERATION_PHRASES:
            if phrase in lowered:
                for callee in callees:
                    _add(callee)
        for word in _IDENTIFIER.findall(lowered):
            if word in OPERATION_APIS:
                for callee in OPERATION_APIS[word]:
                    _add(callee)

        return tuple(ordered), frozenset(seen)


def infer_data_kinds(*texts: str) -> FrozenSet[str]:
    """Return the data-kind tags implied by ``texts``."""
    tokens: Set[str] = set()
    blob = " ".join(text for text in texts if text).lower()
    for text in texts:
        tokens.update(tokenize(text))
    kinds: Set[str] = set()
    for kind, hints in KIND_HINTS.items():
        if kind in tokens or any(hint in tokens for hint in hints):
            kinds.add(kind)
            continue
        if any(hint in blob for hint in hints if len(hint) > 3):
            kinds.add(kind)
    return frozenset(kinds)


def canonicalize_call(qual: str, aliases: _FileAliases) -> str:
    """Normalize a dotted callee using file imports and scientific aliases."""
    if not qual:
        return ""
    parts = [part for part in qual.split(".") if part]
    if not parts:
        return ""
    if len(parts) == 1 and parts[0] in aliases.names:
        return aliases.names[parts[0]]
    if parts[0] in aliases.prefixes:
        replacement = [p for p in aliases.prefixes[parts[0]].split(".") if p]
        parts = replacement + parts[1:]
    elif parts[0] in _MODULE_CANON:
        parts[0] = _MODULE_CANON[parts[0]]
    joined = ".".join(parts)
    if joined in _EXACT_MODULE:
        return _EXACT_MODULE[joined]
    for prefix, canon in _DOTTED_PREFIX_CANON:
        if joined.startswith(prefix):
            joined = canon + joined[len(prefix) :]
            break
    head = joined.split(".", 1)[0]
    if head in _MODULE_CANON:
        rest = joined.split(".", 1)
        joined = _MODULE_CANON[head] + (("." + rest[1]) if len(rest) == 2 else "")
    return joined


def _features_for(
    record: DiscoveredFunction,
    aliases: _FileAliases,
    helpers: Optional[Dict[str, _LocalDef]] = None,
) -> _ChunkFeatures:
    span_comments = _span_comments(record.preview)
    intent = _chunk_intent_tokens(record, span_comments)
    api_seq = _chunk_api_seq(record, aliases, helpers or {})
    grams = _api_grams(api_seq)
    kinds = infer_data_kinds(
        record.name if not _BLOCK_NAME.match(record.name) else "",
        record.docstring,
        record.leading_comment,
        " ".join(span_comments),
        " ".join(record.tags),
        " ".join(api_seq),
        record.module,
    )
    return _ChunkFeatures(
        record=record,
        intent_tokens=tuple(intent),
        intent_set=frozenset(intent),
        api_seq=api_seq,
        api_set=frozenset(api_seq),
        api_grams=grams,
        minhash=_minhash(grams),
        winnow=_winnow_fingerprints(grams),
        kind_tags=kinds,
    )


def _chunk_intent_tokens(
    record: DiscoveredFunction, span_comments: Sequence[str]
) -> List[str]:
    texts: List[str] = []
    if record.chunk_kind != "block" and not _BLOCK_NAME.match(record.name or ""):
        texts.append(record.name)
        if record.class_name:
            texts.append(record.class_name)
    texts.append(record.docstring or "")
    texts.append(record.docstring_first_line or "")
    texts.append(record.leading_comment or "")
    texts.extend(span_comments)
    texts.extend(_assigned_identifier_names(record.preview))
    tokens: List[str] = []
    for text in texts:
        tokens.extend(_intent_tokens_from_text(text))
    return tokens


def _intent_tokens_from_text(text: str) -> List[str]:
    return [tok for tok in tokenize(text) if tok not in STOPWORDS and len(tok) > 1]


def _chunk_api_seq(
    record: DiscoveredFunction,
    aliases: _FileAliases,
    helpers: Dict[str, _LocalDef],
) -> Tuple[str, ...]:
    from_ast = _api_seq_from_source(record.preview, aliases, helpers)
    if from_ast:
        return from_ast
    return tuple(canonicalize_call(name, aliases) for name in record.calls if name)


def _api_seq_from_source(
    source: str,
    aliases: _FileAliases,
    helpers: Optional[Dict[str, _LocalDef]] = None,
) -> Tuple[str, ...]:
    tree = _parse_snippet(source)
    if tree is None:
        return ()
    return _api_seq_from_tree(tree, aliases, helpers or {})


def _api_seq_from_tree(
    tree: ast.AST,
    aliases: _FileAliases,
    helpers: Dict[str, _LocalDef],
    *,
    depth: int = WRAPPER_EXPAND_DEPTH,
    seen: Optional[Set[str]] = None,
) -> Tuple[str, ...]:
    """Walk Call nodes; inline same-file helpers up to ``depth`` hops."""
    seen = seen if seen is not None else set()
    names: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        local = _local_callee_key(node.func)
        if local and local in helpers and local not in seen and depth > 0:
            seen.add(local)
            names.extend(_expand_local_def(helpers[local], helpers, depth - 1, seen))
            continue
        qual = _call_qualname(node.func)
        if not qual:
            continue
        if local and local in helpers:
            continue
        canon = canonicalize_call(qual, aliases)
        if canon:
            names.append(canon)
    return tuple(name for name in names if name)


def _expand_local_def(
    defn: _LocalDef,
    helpers: Dict[str, _LocalDef],
    depth: int,
    seen: Set[str],
) -> List[str]:
    out = list(defn.api_seq)
    if depth < 0:
        return out
    for callee in defn.local_callees:
        if callee in seen or callee not in helpers:
            continue
        seen.add(callee)
        out.extend(_expand_local_def(helpers[callee], helpers, depth - 1, seen))
    return out


def _local_callee_key(func: ast.AST) -> Optional[str]:
    """Same-file call name: ``helper()`` or ``self.filter()`` / ``cls.filter()``."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id in _SELF_NAMES:
            return func.attr
    return None


def _parse_snippet(source: str) -> Optional[ast.AST]:
    if not source or not source.strip():
        return None
    for candidate in (source, textwrap.dedent(source)):
        try:
            return ast.parse(candidate)
        except (SyntaxError, ValueError):
            continue
    return None


def _call_qualname(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parent = _call_qualname(func.value)
        return f"{parent}.{func.attr}" if parent else func.attr
    return ""


def _api_grams(seq: Sequence[str]) -> FrozenSet[str]:
    if not seq:
        return frozenset()
    grams: Set[str] = set()
    grams.update(seq)
    grams.update(name.split(".")[-1] for name in seq if name)
    for k in KGRAM_K:
        if len(seq) >= k:
            for i in range(len(seq) - k + 1):
                grams.add("||".join(seq[i : i + k]))
    return frozenset(grams)


def _minhash(
    grams: Iterable[str], permutations: int = MINHASH_PERMUTATIONS
) -> Tuple[int, ...]:
    items = list(grams)
    if not items:
        return tuple(0 for _ in range(permutations))
    values = [2**64 - 1] * permutations
    for gram in items:
        encoded = gram.encode("utf-8")
        for seed in range(permutations):
            hashed = _blake64(encoded, seed)
            if hashed < values[seed]:
                values[seed] = hashed
    return tuple(values)


def _minhash_jaccard(left: Sequence[int], right: Sequence[int]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    if all(value == 0 for value in left) and all(value == 0 for value in right):
        return 0.0
    equal = sum(a == b for a, b in zip(left, right))
    return equal / len(left)


def _winnow_fingerprints(
    grams: Iterable[str], window: int = WINNOW_WINDOW
) -> FrozenSet[int]:
    """MOSS-style winnowing over a stable ordering of k-gram hashes."""
    hashes = sorted(_blake64(gram.encode("utf-8"), 0) for gram in grams)
    if not hashes:
        return frozenset()
    if len(hashes) <= window:
        return frozenset({min(hashes)})
    fingerprints: Set[int] = set()
    last_idx = -1
    for start in range(len(hashes) - window + 1):
        window_hashes = hashes[start : start + window]
        min_val = window_hashes[-1]
        min_off = window - 1
        for offset, value in enumerate(window_hashes):
            if value <= min_val:
                min_val = value
                min_off = offset
        global_idx = start + min_off
        if global_idx != last_idx:
            fingerprints.add(min_val)
            last_idx = global_idx
    return frozenset(fingerprints)


def _blake64(data: bytes, seed: int) -> int:
    payload = seed.to_bytes(4, "little") + data
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")


def _span_comments(source: str) -> List[str]:
    if not source:
        return []
    comments: List[str] = []
    try:
        tokens = tokenize_mod.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type != tokenize_mod.COMMENT:
                continue
            text = tok.string.lstrip("#").strip()
            if not text or _SKIP_COMMENT.match(text):
                continue
            comments.append(text)
    except (tokenize_mod.TokenError, IndentationError, SyntaxError):
        return comments
    return comments


def _aliases_for_records(
    records: Sequence[DiscoveredFunction],
) -> Dict[str, _FileAliases]:
    paths = {record.source_path for record in records if record.source_path}
    aliases: Dict[str, _FileAliases] = {}
    for path in paths:
        aliases[path] = _aliases_from_path(path)
    return aliases


def _aliases_from_path(path: str) -> _FileAliases:
    if not path or not os.path.isfile(path):
        return _FileAliases()
    try:
        source = open(path, "r", encoding="utf-8").read()
    except (OSError, UnicodeDecodeError):
        return _FileAliases()
    return _aliases_from_source(source)


def _aliases_from_source(source: str) -> _FileAliases:
    aliases = _FileAliases()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return aliases
    empty = _FileAliases()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                canonical = canonicalize_call(alias.name, empty)
                aliases.prefixes[local] = canonical or local
                top = alias.name.split(".")[0]
                aliases.prefixes[top] = canonicalize_call(top, empty) or top
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                qualified = f"{module}.{alias.name}" if module else alias.name
                canonical = canonicalize_call(qualified, empty)
                if _looks_like_module_import(alias.name, module):
                    aliases.prefixes[local] = canonical or local
                else:
                    aliases.names[local] = canonical or qualified
    return aliases


def _looks_like_module_import(imported: str, from_module: str) -> bool:
    if imported in _KNOWN_MODULE_TAILS or imported in _MODULE_CANON:
        return True
    if from_module in _EXACT_MODULE or from_module in _MODULE_CANON:
        return False
    return imported in _EXACT_MODULE


def _assigned_identifier_names(source: str) -> List[str]:
    """Left-hand names in the span — scientists often name the *what*."""
    tree = _parse_snippet(source)
    if tree is None:
        return []
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names.extend(_target_names(target))
        elif isinstance(node, ast.AnnAssign) and node.target is not None:
            names.extend(_target_names(node.target))
    return names


def _target_names(target: ast.AST) -> List[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: List[str] = []
        for elt in target.elts:
            names.extend(_target_names(elt))
        return names
    return []


def _score_pair(
    *,
    query_tokens: Sequence[str],
    query_tf: Counter,
    query_api: FrozenSet[str],
    query_seq: Sequence[str],
    query_grams: FrozenSet[str],
    query_minhash: Sequence[int],
    query_kinds: FrozenSet[str],
    feat: _ChunkFeatures,
    intent_df: Counter,
    n_docs: int,
    run_align: bool = True,
) -> ScoreBreakdown:
    intent = _intent_score(query_tokens, query_tf, feat, intent_df, n_docs)
    api = _api_score(query_api, query_grams, query_minhash, feat)
    kind = _kind_score(query_kinds, feat.kind_tags)
    align = _align_score(query_seq, feat.api_seq) if run_align else 0.0
    total = ALPHA * intent + BETA * api + GAMMA * kind + DELTA * align
    if feat.record.starred:
        total += STAR_BONUS
    return ScoreBreakdown(intent=intent, api=api, kind=kind, align=align, total=total)


def _intent_score(
    query_tokens: Sequence[str],
    query_tf: Counter,
    feat: _ChunkFeatures,
    intent_df: Counter,
    n_docs: int,
) -> float:
    if not query_tokens:
        return 0.0
    query_set = set(query_tokens)
    coverage = len(query_set & feat.intent_set) / len(query_set)
    cosine = _tfidf_cosine(query_tf, Counter(feat.intent_tokens), intent_df, n_docs)
    return INTENT_COVERAGE_WEIGHT * coverage + INTENT_TFIDF_WEIGHT * cosine


def _api_score(
    query_api: FrozenSet[str],
    query_grams: FrozenSet[str],
    query_minhash: Sequence[int],
    feat: _ChunkFeatures,
) -> float:
    if not query_api and not query_grams:
        return 0.0
    overlap = _leaf_f1(query_api, feat.api_set)
    if overlap == 0.0 and query_api:
        overlap = _set_jaccard(query_api, feat.api_set)
    if overlap == 0.0 and query_grams:
        overlap = _set_jaccard(query_grams, feat.api_grams)
    mh = _minhash_jaccard(query_minhash, feat.minhash)
    return API_JACCARD_WEIGHT * overlap + API_MINHASH_WEIGHT * mh


def _leaf_f1(left: FrozenSet[str], right: FrozenSet[str]) -> float:
    left_leaves = _callee_leaves(left)
    right_leaves = _callee_leaves(right)
    if not left_leaves or not right_leaves:
        return 0.0
    inter = len(left_leaves & right_leaves)
    if inter == 0:
        return 0.0
    precision = inter / len(right_leaves)
    recall = inter / len(left_leaves)
    return 2.0 * precision * recall / (precision + recall)


def _callee_leaves(names: Iterable[str]) -> FrozenSet[str]:
    return frozenset(name.split(".")[-1].lower() for name in names if name)


def _set_jaccard(left: FrozenSet[str], right: FrozenSet[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _kind_score(query_kinds: FrozenSet[str], doc_kinds: FrozenSet[str]) -> float:
    if not query_kinds:
        return 0.0
    return len(query_kinds & doc_kinds) / len(query_kinds)


def _tfidf_cosine(
    query_tf: Counter, doc_tf: Counter, df: Counter, n_docs: int
) -> float:
    if not query_tf or not doc_tf:
        return 0.0
    n = n_docs or 1

    def idf(term: str) -> float:
        return math.log((n + 1) / (df.get(term, 0) + 1))

    q_len = sum(query_tf.values()) or 1
    d_len = sum(doc_tf.values()) or 1
    q_vec = {term: (count / q_len) * idf(term) for term, count in query_tf.items()}
    d_vec = {term: (count / d_len) * idf(term) for term, count in doc_tf.items()}
    shared = set(q_vec) & set(d_vec)
    if not shared:
        return 0.0
    dot = sum(q_vec[term] * d_vec[term] for term in shared)
    q_norm = math.sqrt(sum(value * value for value in q_vec.values()))
    d_norm = math.sqrt(sum(value * value for value in d_vec.values()))
    if q_norm == 0.0 or d_norm == 0.0:
        return 0.0
    return dot / (q_norm * d_norm)


def _looks_like_code(query: str) -> bool:
    if "(" in query or "\n" in query:
        return True
    return bool(_CODE_LIKE.search(query)) and ("." in query or "=" in query)


def _equiv_index(
    groups: Sequence[FrozenSet[str]],
) -> Dict[str, FrozenSet[str]]:
    index: Dict[str, FrozenSet[str]] = {}
    for group in groups:
        lowered = frozenset(name.lower() for name in group)
        for name in lowered:
            index[name] = lowered
            leaf = name.split(".")[-1]
            index.setdefault(leaf, lowered)
    return index


_API_EQUIV = _equiv_index(API_EQUIV_GROUPS)


def _api_similarity(left: str, right: str) -> float:
    if not left or not right:
        return ALIGN_MISMATCH
    a, b = left.lower(), right.lower()
    if a == b:
        return ALIGN_MATCH
    if a.split(".")[-1] == b.split(".")[-1]:
        return ALIGN_SUBST
    group_a = _API_EQUIV.get(a) or _API_EQUIV.get(a.split(".")[-1])
    group_b = _API_EQUIV.get(b) or _API_EQUIV.get(b.split(".")[-1])
    if group_a and group_b and not group_a.isdisjoint(group_b):
        return ALIGN_SUBST
    return ALIGN_MISMATCH


def _smith_waterman(query_seq: Sequence[str], doc_seq: Sequence[str]) -> float:
    """Local alignment; cheap because callee sequences are short."""
    n, m = len(query_seq), len(doc_seq)
    if n == 0 or m == 0:
        return 0.0
    prev = [0.0] * (m + 1)
    best = 0.0
    for i in range(1, n + 1):
        current = [0.0] * (m + 1)
        q_name = query_seq[i - 1]
        for j in range(1, m + 1):
            diag = prev[j - 1] + _api_similarity(q_name, doc_seq[j - 1])
            delete = prev[j] + ALIGN_GAP
            insert = current[j - 1] + ALIGN_GAP
            value = max(0.0, diag, delete, insert)
            current[j] = value
            if value > best:
                best = value
        prev = current
    return best


def _align_score(query_seq: Sequence[str], doc_seq: Sequence[str]) -> float:
    if not query_seq or not doc_seq:
        return 0.0
    raw = _smith_waterman(query_seq, doc_seq)
    denom = ALIGN_MATCH * len(query_seq)
    if denom <= 0:
        return 0.0
    return min(1.0, max(0.0, raw / denom))


def _split_calls_from_tree(
    tree: ast.AST, aliases: _FileAliases
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Library APIs vs same-file callee names (no expansion)."""
    lib: List[str] = []
    local: List[str] = []
    seen_local: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        key = _local_callee_key(node.func)
        if key and key not in seen_local:
            seen_local.add(key)
            local.append(key)
        if key:
            continue
        qual = _call_qualname(node.func)
        if qual:
            canon = canonicalize_call(qual, aliases)
            if canon:
                lib.append(canon)
    return tuple(lib), tuple(local)


def _local_defs_from_source(source: str, aliases: _FileAliases) -> Dict[str, _LocalDef]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    defs: Dict[str, _LocalDef] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        snippet = ast.Module(body=list(node.body), type_ignores=[])
        lib_seq, local_callees = _split_calls_from_tree(snippet, aliases)
        defs[node.name] = _LocalDef(
            name=node.name, api_seq=lib_seq, local_callees=local_callees
        )
    return defs


def _local_defs_for_records(
    records: Sequence[DiscoveredFunction],
    aliases_by_path: Dict[str, _FileAliases],
) -> Dict[str, Dict[str, _LocalDef]]:
    """Same-file helper map: parse each path once; fill gaps from chunk previews."""
    by_path: Dict[str, List[DiscoveredFunction]] = defaultdict(list)
    for record in records:
        if record.source_path:
            by_path[record.source_path].append(record)

    result: Dict[str, Dict[str, _LocalDef]] = {}
    for path, group in by_path.items():
        aliases = aliases_by_path.get(path, _FileAliases())
        defs: Dict[str, _LocalDef] = {}
        if path and os.path.isfile(path):
            try:
                source = open(path, "r", encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                source = ""
            if source:
                defs.update(_local_defs_from_source(source, aliases))
        for record in group:
            if not record.name or record.name in defs:
                continue
            tree = _parse_snippet(record.preview)
            if tree is None:
                continue
            lib_seq, local_callees = _split_calls_from_tree(tree, aliases)
            defs[record.name] = _LocalDef(
                name=record.name, api_seq=lib_seq, local_callees=local_callees
            )
        result[path] = defs
    return result


_MAX_RELATIVE_INHERIT = 32


def _inherit_relative_import_apis(
    helpers_by_path: Dict[str, Dict[str, _LocalDef]],
) -> None:
    """Copy API sequences across ``from .mod import name`` (depth 1–2).

    Mutates ``helpers_by_path`` in place.  Skips star-imports, absolute
    imports, and anything that does not resolve to a file already in the
    index so a messy package cannot explode the walk.
    """
    by_abs: Dict[str, str] = {}
    for path in helpers_by_path:
        try:
            by_abs[os.path.abspath(path)] = path
        except (OSError, ValueError):
            continue

    for path, helpers in helpers_by_path.items():
        if not path or not os.path.isfile(path):
            continue
        try:
            source = open(path, "r", encoding="utf-8").read()
            tree = ast.parse(source)
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
            continue
        inherited = 0
        for node in tree.body:
            if inherited >= _MAX_RELATIVE_INHERIT:
                break
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level not in (1, 2):
                continue
            resolved = _resolve_relative_module(path, node.module, node.level)
            if not resolved:
                continue
            try:
                remote_key = by_abs.get(os.path.abspath(resolved))
            except (OSError, ValueError):
                remote_key = None
            if remote_key is None:
                continue
            remote = helpers_by_path.get(remote_key) or {}
            for alias in node.names:
                if inherited >= _MAX_RELATIVE_INHERIT:
                    break
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                if local in helpers:
                    continue
                defn = remote.get(alias.name)
                if defn is None:
                    continue
                helpers[local] = defn
                inherited += 1


def _resolve_relative_module(
    importer: str, module: Optional[str], level: int
) -> Optional[str]:
    """Return ``filters.py`` for ``from .filters import …`` in ``pkg/a.py``."""
    here = os.path.dirname(os.path.abspath(importer))
    for _ in range(max(level - 1, 0)):
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent
    if module:
        here = os.path.join(here, *module.split("."))
    py_file = here + ".py"
    init_file = os.path.join(here, "__init__.py")
    if os.path.isfile(py_file):
        return py_file
    if os.path.isfile(init_file):
        return init_file
    return None


def parse_span_spec(spec: str) -> Tuple[str, int, int]:
    """Parse ``path.py:12-40`` into ``(path, start, end)`` (1-based, inclusive)."""
    if not spec or not spec.strip():
        raise ValueError("empty span spec")
    match = _SPAN_TAIL.search(spec.strip())
    if not match:
        raise ValueError(f"invalid span spec {spec!r}; expected path.py:START-END")
    path = spec.strip()[: match.start()]
    start = int(match.group(1))
    end = int(match.group(2))
    if not path or start < 1 or end < start:
        raise ValueError(f"invalid span spec {spec!r}")
    return path, start, end


def _path_matches(record_path: str, spec_path: str) -> bool:
    if not record_path or not spec_path:
        return False
    rec = os.path.normpath(record_path)
    spec = os.path.normpath(spec_path)
    if rec == spec:
        return True
    if rec.endswith(os.sep + spec) or rec.endswith(spec):
        return True
    try:
        if os.path.abspath(rec) == os.path.abspath(spec):
            return True
    except (OSError, ValueError):
        pass
    spec_norm = spec.replace("/", os.sep)
    return os.path.basename(rec) == os.path.basename(spec) and os.sep not in spec_norm


def _spans_overlap(record: DiscoveredFunction, start: int, end: int) -> bool:
    rec_start = record.lineno or 0
    rec_end = record.end_lineno or rec_start
    return rec_start <= end and rec_end >= start


def find_record_for_span(
    records: Sequence[DiscoveredFunction], path: str, start: int, end: int
) -> Optional[DiscoveredFunction]:
    """Tightest overlapping indexed chunk for ``path:start-end``."""
    matches: List[DiscoveredFunction] = [
        record
        for record in records
        if _path_matches(record.source_path, path)
        and _spans_overlap(record, start, end)
    ]
    if not matches:
        return None

    def _tightness(record: DiscoveredFunction) -> Tuple[int, int, int]:
        rec_start = record.lineno or 0
        rec_end = record.end_lineno or rec_start
        exact = 0 if rec_start == start and rec_end == end else 1
        span = rec_end - rec_start
        extra = abs(rec_start - start) + abs(rec_end - end)
        return (exact, span, extra)

    matches.sort(key=_tightness)
    return matches[0]


def find_record_for_kind(
    records: Sequence[DiscoveredFunction], kind: str
) -> Optional[DiscoveredFunction]:
    needle = (kind or "").strip()
    if not needle:
        return None
    for record in records:
        if record.kind == needle or record.qualified_name == needle:
            return record
    return None


def _views_from_record(record: DiscoveredFunction) -> QueryViews:
    aliases = _aliases_from_path(record.source_path)
    helpers: Dict[str, _LocalDef] = {}
    if record.source_path and os.path.isfile(record.source_path):
        try:
            source = open(record.source_path, "r", encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            source = ""
        if source:
            helpers = _local_defs_from_source(source, aliases)
    comments = _span_comments(record.preview)
    intent = tuple(_chunk_intent_tokens(record, comments))
    api_seq = _chunk_api_seq(record, aliases, helpers)
    return QueryViews(
        intent=intent,
        api_seq=api_seq,
        api_set=frozenset(api_seq),
        kinds=infer_data_kinds(
            record.name if not _BLOCK_NAME.match(record.name) else "",
            record.docstring,
            record.leading_comment,
            " ".join(comments),
            " ".join(record.tags),
            " ".join(api_seq),
        ),
        text=record.document_text,
        exclude_key=(
            record.source_path,
            record.lineno,
            record.end_lineno or record.lineno,
        ),
    )


def _views_from_file_span(path: str, start: int, end: int) -> Optional[QueryViews]:
    try:
        lines = open(path, "r", encoding="utf-8").read().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    snippet = "\n".join(lines[start - 1 : end])
    if not snippet.strip():
        return None
    aliases = _aliases_from_path(path)
    helpers: Dict[str, _LocalDef] = {}
    try:
        source = open(path, "r", encoding="utf-8").read()
        helpers = _local_defs_from_source(source, aliases)
    except (OSError, UnicodeDecodeError):
        pass
    comments = _span_comments(snippet)
    intent_tokens: List[str] = list(_intent_tokens_from_text(" ".join(comments)))
    for name in _assigned_identifier_names(snippet):
        intent_tokens.extend(_intent_tokens_from_text(name))
    api_seq = _api_seq_from_source(snippet, aliases, helpers)
    kinds = infer_data_kinds(snippet, " ".join(api_seq))
    abs_path = os.path.abspath(path) if os.path.isfile(path) else path
    return QueryViews(
        intent=tuple(intent_tokens),
        api_seq=api_seq,
        api_set=frozenset(api_seq),
        kinds=kinds,
        text=snippet,
        exclude_key=(abs_path, start, end),
    )


def _same_span(record: DiscoveredFunction, key: Optional[Tuple[str, int, int]]) -> bool:
    if key is None:
        return False
    path, start, end = key
    rec_end = record.end_lineno or record.lineno
    if (
        record.lineno == start
        and rec_end == end
        and _path_matches(record.source_path, path)
    ):
        return True
    return False


def _exclude_query_chunk(
    hits: List[ScoredHit], key: Optional[Tuple[str, int, int]]
) -> List[ScoredHit]:
    if key is None:
        return hits
    return [hit for hit in hits if not _same_span(hit.record, key)]


def _equiv_index(
    groups: Sequence[FrozenSet[str]],
) -> Dict[str, FrozenSet[str]]:
    index: Dict[str, FrozenSet[str]] = {}
    for group in groups:
        lowered = frozenset(name.lower() for name in group)
        for name in lowered:
            index[name] = lowered
            leaf = name.split(".")[-1]
            index.setdefault(leaf, lowered)
    return index


_API_EQUIV = _equiv_index(API_EQUIV_GROUPS)


def _api_similarity(left: str, right: str) -> float:
    if not left or not right:
        return ALIGN_MISMATCH
    a, b = left.lower(), right.lower()
    if a == b:
        return ALIGN_MATCH
    if a.split(".")[-1] == b.split(".")[-1]:
        return ALIGN_SUBST
    group_a = _API_EQUIV.get(a) or _API_EQUIV.get(a.split(".")[-1])
    group_b = _API_EQUIV.get(b) or _API_EQUIV.get(b.split(".")[-1])
    if group_a and group_b and not group_a.isdisjoint(group_b):
        return ALIGN_SUBST
    return ALIGN_MISMATCH


def _smith_waterman(query_seq: Sequence[str], doc_seq: Sequence[str]) -> float:
    """Local alignment; cheap because callee sequences are short."""
    n, m = len(query_seq), len(doc_seq)
    if n == 0 or m == 0:
        return 0.0
    prev = [0.0] * (m + 1)
    best = 0.0
    for i in range(1, n + 1):
        current = [0.0] * (m + 1)
        q_name = query_seq[i - 1]
        for j in range(1, m + 1):
            diag = prev[j - 1] + _api_similarity(q_name, doc_seq[j - 1])
            delete = prev[j] + ALIGN_GAP
            insert = current[j - 1] + ALIGN_GAP
            value = max(0.0, diag, delete, insert)
            current[j] = value
            if value > best:
                best = value
        prev = current
    return best


def _align_score(query_seq: Sequence[str], doc_seq: Sequence[str]) -> float:
    if not query_seq or not doc_seq:
        return 0.0
    raw = _smith_waterman(query_seq, doc_seq)
    denom = ALIGN_MATCH * len(query_seq)
    if denom <= 0:
        return 0.0
    return min(1.0, max(0.0, raw / denom))


def _split_calls_from_tree(
    tree: ast.AST, aliases: _FileAliases
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Library APIs vs same-file callee names (no expansion)."""
    lib: List[str] = []
    local: List[str] = []
    seen_local: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        key = _local_callee_key(node.func)
        if key and key not in seen_local:
            seen_local.add(key)
            local.append(key)
        if key:
            continue
        qual = _call_qualname(node.func)
        if qual:
            canon = canonicalize_call(qual, aliases)
            if canon:
                lib.append(canon)
    return tuple(lib), tuple(local)


def _local_defs_from_source(source: str, aliases: _FileAliases) -> Dict[str, _LocalDef]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    defs: Dict[str, _LocalDef] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        snippet = ast.Module(body=list(node.body), type_ignores=[])
        lib_seq, local_callees = _split_calls_from_tree(snippet, aliases)
        defs[node.name] = _LocalDef(
            name=node.name, api_seq=lib_seq, local_callees=local_callees
        )
    return defs


def _local_defs_for_records(
    records: Sequence[DiscoveredFunction],
    aliases_by_path: Dict[str, _FileAliases],
) -> Dict[str, Dict[str, _LocalDef]]:
    """Same-file helper map: parse each path once; fill gaps from chunk previews."""
    by_path: Dict[str, List[DiscoveredFunction]] = defaultdict(list)
    for record in records:
        if record.source_path:
            by_path[record.source_path].append(record)

    result: Dict[str, Dict[str, _LocalDef]] = {}
    for path, group in by_path.items():
        aliases = aliases_by_path.get(path, _FileAliases())
        defs: Dict[str, _LocalDef] = {}
        if path and os.path.isfile(path):
            try:
                source = open(path, "r", encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                source = ""
            if source:
                defs.update(_local_defs_from_source(source, aliases))
        for record in group:
            if not record.name or record.name in defs:
                continue
            tree = _parse_snippet(record.preview)
            if tree is None:
                continue
            lib_seq, local_callees = _split_calls_from_tree(tree, aliases)
            defs[record.name] = _LocalDef(
                name=record.name, api_seq=lib_seq, local_callees=local_callees
            )
        result[path] = defs
    return result


def parse_span_spec(spec: str) -> Tuple[str, int, int]:
    """Parse ``path.py:12-40`` into ``(path, start, end)`` (1-based, inclusive)."""
    if not spec or not spec.strip():
        raise ValueError("empty span spec")
    match = _SPAN_TAIL.search(spec.strip())
    if not match:
        raise ValueError(f"invalid span spec {spec!r}; expected path.py:START-END")
    path = spec.strip()[: match.start()]
    start = int(match.group(1))
    end = int(match.group(2))
    if not path or start < 1 or end < start:
        raise ValueError(f"invalid span spec {spec!r}")
    return path, start, end


def _path_matches(record_path: str, spec_path: str) -> bool:
    if not record_path or not spec_path:
        return False
    rec = os.path.normpath(record_path)
    spec = os.path.normpath(spec_path)
    if rec == spec:
        return True
    if rec.endswith(os.sep + spec) or rec.endswith(spec):
        return True
    try:
        if os.path.abspath(rec) == os.path.abspath(spec):
            return True
    except (OSError, ValueError):
        pass
    return os.path.basename(rec) == os.path.basename(
        spec
    ) and os.sep not in spec.replace("/", os.sep)


def _spans_overlap(record: DiscoveredFunction, start: int, end: int) -> bool:
    rec_start = record.lineno or 0
    rec_end = record.end_lineno or rec_start
    return rec_start <= end and rec_end >= start


def find_record_for_span(
    records: Sequence[DiscoveredFunction], path: str, start: int, end: int
) -> Optional[DiscoveredFunction]:
    """Tightest overlapping indexed chunk for ``path:start-end``."""
    matches: List[DiscoveredFunction] = [
        record
        for record in records
        if _path_matches(record.source_path, path)
        and _spans_overlap(record, start, end)
    ]
    if not matches:
        return None

    def _tightness(record: DiscoveredFunction) -> Tuple[int, int, int]:
        rec_start = record.lineno or 0
        rec_end = record.end_lineno or rec_start
        exact = 0 if rec_start == start and rec_end == end else 1
        span = rec_end - rec_start
        extra = abs(rec_start - start) + abs(rec_end - end)
        return (exact, span, extra)

    matches.sort(key=_tightness)
    return matches[0]


def find_record_for_kind(
    records: Sequence[DiscoveredFunction], kind: str
) -> Optional[DiscoveredFunction]:
    needle = (kind or "").strip()
    if not needle:
        return None
    for record in records:
        if record.kind == needle or record.qualified_name == needle:
            return record
        if needle.startswith("repo.") and record.kind == needle:
            return record
    return None


def _views_from_record(record: DiscoveredFunction) -> QueryViews:
    aliases = _aliases_from_path(record.source_path)
    helpers = {}
    if record.source_path and os.path.isfile(record.source_path):
        try:
            source = open(record.source_path, "r", encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            source = ""
        if source:
            helpers = _local_defs_from_source(source, aliases)
    comments = _span_comments(record.preview)
    intent = tuple(_chunk_intent_tokens(record, comments))
    api_seq = _chunk_api_seq(record, aliases, helpers)
    return QueryViews(
        intent=intent,
        api_seq=api_seq,
        api_set=frozenset(api_seq),
        kinds=infer_data_kinds(
            record.name if not _BLOCK_NAME.match(record.name) else "",
            record.docstring,
            record.leading_comment,
            " ".join(comments),
            " ".join(record.tags),
            " ".join(api_seq),
        ),
        text=record.document_text,
        exclude_key=(
            record.source_path,
            record.lineno,
            record.end_lineno or record.lineno,
        ),
    )


def _views_from_file_span(path: str, start: int, end: int) -> Optional[QueryViews]:
    try:
        lines = open(path, "r", encoding="utf-8").read().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    snippet = "\n".join(lines[start - 1 : end])
    if not snippet.strip():
        return None
    aliases = _aliases_from_path(path)
    helpers = {}
    try:
        source = open(path, "r", encoding="utf-8").read()
        helpers = _local_defs_from_source(source, aliases)
    except (OSError, UnicodeDecodeError):
        pass
    comments = _span_comments(snippet)
    intent = tuple(_intent_tokens_from_text(" ".join(comments)))
    intent += tuple(
        _intent_tokens_from_text(name) for name in _assigned_identifier_names(snippet)
    )
    # flatten assigned-name tokens
    flat_intent: List[str] = []
    for item in intent:
        if isinstance(item, tuple):
            flat_intent.extend(item)
        else:
            flat_intent.append(item)
    api_seq = _api_seq_from_source(snippet, aliases, helpers)
    kinds = infer_data_kinds(snippet, " ".join(api_seq))
    return QueryViews(
        intent=tuple(flat_intent),
        api_seq=api_seq,
        api_set=frozenset(api_seq),
        kinds=kinds,
        text=snippet,
        exclude_key=(
            os.path.abspath(path) if os.path.isfile(path) else path,
            start,
            end,
        ),
    )


def _same_span(record: DiscoveredFunction, key: Optional[Tuple[str, int, int]]) -> bool:
    if key is None:
        return False
    path, start, end = key
    rec_end = record.end_lineno or record.lineno
    if (
        record.lineno == start
        and rec_end == end
        and _path_matches(record.source_path, path)
    ):
        return True
    return False


def _exclude_query_chunk(
    hits: List[ScoredHit], key: Optional[Tuple[str, int, int]]
) -> List[ScoredHit]:
    if key is None:
        return hits
    return [hit for hit in hits if not _same_span(hit.record, key)]
