"""Shared vector retrieval for RAG and Gemini agent tool calls."""

from config.settings import Settings
from rag.embeddings import embed_texts
from rag.query import search_chunks
from rag.rerank import rerank
from rag.schemas import RagFilters, RetrievedChunk


def retrieve_best_chunks(
    query: str,
    settings: Settings,
    base: RagFilters | None = None,
) -> list[RetrievedChunk]:
    """
    Qdrant search with progressively looser metadata filters (same strategy as legacy router path).
    """
    if base is None:
        base = RagFilters()
    qvec = embed_texts([query])[0]
    attempts: list[RagFilters] = [
        base,
        RagFilters(
            product=None,
            language=base.language,
            doc_type=base.doc_type,
            department=base.department,
        ),
        RagFilters(
            product=None,
            language=base.language,
            doc_type=None,
            department=None,
        ),
        RagFilters(),
    ]
    seen: set[str] = set()
    for filt in attempts:
        key = filt.model_dump_json(exclude_none=True)
        if key in seen:
            continue
        seen.add(key)
        found = search_chunks(
            question_vector=qvec,
            filters=filt,
            top_k=settings.rag_search_top_k,
        )
        best = rerank(query, found, settings.rag_final_top_k)
        if best and best[0].score >= settings.rag_min_score:
            return best
    return []


def retrieve_prefetch_chunks(
    query: str,
    settings: Settings,
    base: RagFilters | None = None,
) -> list[RetrievedChunk]:
    """
    Prefer authoritative KB text for product questions even when scores sit slightly below rag_min_score.
    Returns the best non-empty hit from the same filter ladder, or [] if Qdrant returned nothing.
    """
    if base is None:
        base = RagFilters()
    qvec = embed_texts([query])[0]
    attempts: list[RagFilters] = [
        base,
        RagFilters(
            product=None,
            language=base.language,
            doc_type=base.doc_type,
            department=base.department,
        ),
        RagFilters(
            product=None,
            language=base.language,
            doc_type=None,
            department=None,
        ),
        RagFilters(),
    ]
    seen: set[str] = set()
    best_loose: list[RetrievedChunk] = []
    loose_score = -1.0
    for filt in attempts:
        key = filt.model_dump_json(exclude_none=True)
        if key in seen:
            continue
        seen.add(key)
        found = search_chunks(
            question_vector=qvec,
            filters=filt,
            top_k=settings.rag_search_top_k,
        )
        best = rerank(query, found, settings.rag_final_top_k)
        if not best:
            continue
        if best[0].score >= settings.rag_min_score:
            return best
        if best[0].score > loose_score:
            loose_score = best[0].score
            best_loose = best
    if best_loose and loose_score >= settings.rag_prefetch_min_score:
        return best_loose
    return []


def format_chunks_for_tool(chunks: list[RetrievedChunk]) -> str:
    """Plain-text context for LLM tool result."""
    parts: list[str] = []
    for i, c in enumerate(chunks, start=1):
        src = c.metadata.get("source", "?")
        parts.append(f"[{i}] source={src}\n{c.text}")
    return "\n\n---\n\n".join(parts) if parts else "(no matching passages in the knowledge base)"
