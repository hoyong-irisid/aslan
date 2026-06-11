from qdrant_client import QdrantClient
from qdrant_client.http import models as rest

from config.settings import get_settings
from rag.filters import qdrant_filter
from rag.schemas import RagFilters, RetrievedChunk


def get_client() -> QdrantClient:
    settings = get_settings()
    if settings.qdrant_api_key:
        return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    return QdrantClient(url=settings.qdrant_url)


def search_chunks(
    *,
    question_vector: list[float],
    filters: RagFilters | None,
    top_k: int,
    collection_name: str | None = None,
    exclude_partner: bool = False,
) -> list[RetrievedChunk]:
    settings = get_settings()
    client = get_client()
    qf: rest.Filter | None = None
    if filters is not None or exclude_partner:
        qf = qdrant_filter(filters or RagFilters(), exclude_partner=exclude_partner)

    hits = client.query_points(
        collection_name=collection_name or settings.qdrant_collection,
        query=question_vector,
        limit=top_k,
        query_filter=qf,
        with_payload=True,
    ).points

    out: list[RetrievedChunk] = []
    for p in hits:
        payload = p.payload or {}
        text = str(payload.get("text", ""))
        meta = {k: v for k, v in payload.items() if k != "text"}
        score = float(p.score) if p.score is not None else 0.0
        out.append(RetrievedChunk(text=text, score=score, metadata=meta))
    return out


def dummy_rerank(question: str, chunks: list[RetrievedChunk], final_k: int) -> list[RetrievedChunk]:
    """Placeholder reranker: trim to final_k. Replace with cross-encoder or API rerank."""
    _ = question
    return chunks[:final_k]
