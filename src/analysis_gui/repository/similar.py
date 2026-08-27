"""Offline-first similar-code search over discovered library chunks.

The default ranker is :class:`~analysis_gui.repository.matching.ApiIntentIndex`
(``ApiIntentMatch``): dual-view retrieval over comments/names (intent) and
normalized API-call n-grams (behavior), plus Smith–Waterman sequence
alignment, a data-kind prior, and same-file wrapper expansion.  See that
module's docstring for the formula, index structure, and an honest novelty
claim — the pieces are classic IR/clone-detection; the combination is for
turning lab code into pipeline nodes.

The previous TF-IDF + Jaccard bag-of-tokens ranker remains as
``legacy_tfidf`` (CLI: ``--legacy-tfidf``) and as the empty-candidate
fallback so recall does not collapse.

``similar --from-span path.py:12-40`` and ``--from-kind repo.module.func``
query by a chunk's intent+api+kind instead of a free-text string.

No network is required.  If ``OPENAI_API_KEY`` is set *and* the ``openai``
package is importable, embeddings may rerank the top 50 ApiIntentMatch
candidates; any failure falls back to the lexical order.  Tests never take
that path: embeddings are skipped when ``PYTEST_CURRENT_TEST`` is set.
"""

from __future__ import annotations

import math
import os
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .learn import kind_description
from .matching import (
    RANKER_API_INTENT,
    RANKER_LEGACY,
    RANKER_NAME,
    ApiIntentIndex,
    QueryResult,
    ScoredHit,
    ScoreBreakdown,
    parse_span_spec,
    find_record_for_span,
    find_record_for_kind,
)
from .scan import DiscoveredFunction, tokenize

EMBEDDING_RERANK_POOL = 50


def similar_functions(
    query: str,
    records: Sequence[DiscoveredFunction],
    *,
    limit: int = 20,
    allow_embeddings: bool = True,
    ranker: str = RANKER_API_INTENT,
    match_index: Optional[ApiIntentIndex] = None,
    from_span: Optional[str] = None,
    from_kind: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Rank ``records`` for ``query`` and return JSON-ready hits.

    Each hit includes the candidate node-kind description so a client can
    construct a ``custom_code`` node without a second round trip, plus a
    ``score_breakdown`` with ``intent``, ``api``, ``kind``, ``align``, and ``total``.
    """
    return run_similar(
        query,
        records,
        limit=limit,
        allow_embeddings=allow_embeddings,
        ranker=ranker,
        match_index=match_index,
        from_span=from_span,
        from_kind=from_kind,
    )["hits"]


def run_similar(
    query: str,
    records: Sequence[DiscoveredFunction],
    *,
    limit: int = 20,
    allow_embeddings: bool = True,
    ranker: str = RANKER_API_INTENT,
    match_index: Optional[ApiIntentIndex] = None,
    from_span: Optional[str] = None,
    from_kind: Optional[str] = None,
) -> Dict[str, Any]:
    """Rank ``records`` and return hits plus candidate-generation stats."""
    result = rank_query(
        query,
        records,
        ranker=ranker,
        match_index=match_index,
        from_span=from_span,
        from_kind=from_kind,
    )
    scored = result.hits
    reranked = False
    embed_query = _chunk_query_text(records, from_span=from_span, from_kind=from_kind)
    if not embed_query:
        embed_query = query
    if scored and allow_embeddings:
        pool = scored[:EMBEDDING_RERANK_POOL]
        reranked_hits = _try_embedding_rerank(embed_query or query, pool)
        if reranked_hits is not None:
            scored = reranked_hits + scored[EMBEDDING_RERANK_POOL:]
            reranked = True

    used_ranker = RANKER_LEGACY if ranker == RANKER_LEGACY else RANKER_NAME
    if result.stats.used_fallback and ranker != RANKER_LEGACY:
        used_ranker = RANKER_NAME

    hits: List[Dict[str, Any]] = []
    for item in scored[: max(limit, 0)]:
        payload = kind_description(item.record)
        payload["score"] = round(float(item.breakdown.total), 6)
        payload["score_breakdown"] = item.breakdown.to_dict()
        payload["ranker"] = used_ranker
        payload["reranked"] = reranked
        hits.append(payload)
    return {
        "hits": hits,
        "reranked": reranked,
        "ranker": used_ranker,
        "candidates_examined": result.stats.candidates_examined,
        "used_fallback": result.stats.used_fallback,
        "indexed": result.stats.corpus_size,
        "alignments_scored": getattr(result.stats, "alignments_scored", 0),
    }


def rank_query(
    query: str,
    records: Sequence[DiscoveredFunction],
    *,
    ranker: str = RANKER_API_INTENT,
    match_index: Optional[ApiIntentIndex] = None,
    from_span: Optional[str] = None,
    from_kind: Optional[str] = None,
) -> QueryResult:
    """Run the named ranker and return hits plus candidate-generation stats."""
    if ranker == RANKER_LEGACY:
        legacy_text = (
            _chunk_query_text(records, from_span=from_span, from_kind=from_kind)
            or query
        )
        legacy = rank_records_legacy(legacy_text, records)
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
        ]
        from .matching import QueryStats

        return QueryResult(
            hits=hits,
            stats=QueryStats(
                candidates_examined=len(records),
                corpus_size=len(records),
                used_fallback=False,
                alignments_scored=0,
                ranker=RANKER_LEGACY,
            ),
        )

    index = match_index if match_index is not None else ApiIntentIndex.build(records)
    return index.query(
        query,
        from_span=from_span,
        from_kind=from_kind,
        fallback_records=records,
        fallback_ranker=rank_records_legacy,
    )


def _chunk_query_text(
    records: Sequence[DiscoveredFunction],
    *,
    from_span: Optional[str] = None,
    from_kind: Optional[str] = None,
) -> str:
    """Document text of the seed chunk for embeddings / legacy fallback."""
    record = None
    if from_span:
        try:
            path, start, end = parse_span_spec(from_span)
        except ValueError:
            path, start, end = "", 0, 0
        if path:
            record = find_record_for_span(records, path, start, end)
    elif from_kind:
        record = find_record_for_kind(records, from_kind)
    return record.document_text if record is not None else ""


def rank_records(
    query: str, records: Sequence[DiscoveredFunction]
) -> List[Tuple[float, DiscoveredFunction]]:
    """Return ``(score, record)`` pairs, highest first, zeros dropped.

    Default is ApiIntentMatch.  Use :func:`rank_records_legacy` for the
    original TF-IDF + Jaccard corpus scan.
    """
    result = rank_query(query, records)
    return [(hit.breakdown.total, hit.record) for hit in result.hits]


def rank_records_legacy(
    query: str, records: Sequence[DiscoveredFunction]
) -> List[Tuple[float, DiscoveredFunction]]:
    """Original brute-force TF-IDF cosine plus Jaccard over ``document_text``."""
    if not query or not query.strip() or not records:
        return []

    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    documents = [tokenize(record.document_text) for record in records]
    tfidf = _tfidf_cosines(query_tokens, documents)
    query_set = set(query_tokens)

    ranked: List[Tuple[float, DiscoveredFunction]] = []
    for record, doc_tokens, cosine in zip(records, documents, tfidf):
        doc_set = set(doc_tokens)
        union = query_set | doc_set
        jaccard = (len(query_set & doc_set) / len(union)) if union else 0.0
        name_tokens = set(
            tokenize(record.name)
            + tokenize(record.display_name)
            + tokenize(record.leading_comment)
        )
        name_hits = len(query_set & name_tokens)
        tag_hits = len(query_set & set(record.tags))
        score = 0.65 * cosine + 0.25 * jaccard + 0.08 * name_hits + 0.02 * tag_hits
        if record.starred:
            score += 0.02
        if score > 0:
            ranked.append((score, record))

    ranked.sort(key=lambda item: (-item[0], item[1].qualified_name))
    return ranked


def _tfidf_cosines(
    query_tokens: Sequence[str], documents: Sequence[Sequence[str]]
) -> List[float]:
    df: Counter[str] = Counter()
    for tokens in documents:
        df.update(set(tokens))
    n_docs = len(documents) or 1

    def vector(tokens: Sequence[str]) -> Dict[str, float]:
        if not tokens:
            return {}
        tf = Counter(tokens)
        length = len(tokens)
        return {
            token: (count / length) * math.log((n_docs + 1) / (df.get(token, 0) + 1))
            for token, count in tf.items()
        }

    query_vec = vector(query_tokens)
    scores = []
    for tokens in documents:
        scores.append(_cosine(query_vec, vector(tokens)))
    return scores


def _cosine(left: Dict[str, float], right: Dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    dot = sum(left[key] * right[key] for key in shared)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _try_embedding_rerank(
    query: str, ranked: Sequence[ScoredHit]
) -> Optional[List[ScoredHit]]:
    """Optionally rerank the top candidate pool with OpenAI embeddings."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    texts = [query] + [item.record.document_text for item in ranked]
    try:
        client = OpenAI(api_key=api_key)
        response = client.embeddings.create(model="text-embedding-3-small", input=texts)
        vectors = [item.embedding for item in response.data]
    except Exception:
        return None

    if len(vectors) != len(texts):
        return None

    query_vec = vectors[0]
    rescored: List[ScoredHit] = []
    for item, vector in zip(ranked, vectors[1:]):
        embedding = _dot_unit(query_vec, vector)
        total = 0.4 * item.breakdown.total + 0.6 * embedding
        breakdown = ScoreBreakdown(
            intent=item.breakdown.intent,
            api=item.breakdown.api,
            kind=item.breakdown.kind,
            align=item.breakdown.align,
            total=total,
        )
        rescored.append(ScoredHit(record=item.record, breakdown=breakdown))
    rescored.sort(key=lambda item: (-item.breakdown.total, item.record.qualified_name))
    return rescored


def _dot_unit(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
