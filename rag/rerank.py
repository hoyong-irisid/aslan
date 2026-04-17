from rag.query import dummy_rerank
from rag.schemas import RetrievedChunk

__all__ = ["rerank"]


def rerank(question: str, chunks: list[RetrievedChunk], final_k: int) -> list[RetrievedChunk]:
    return dummy_rerank(question, chunks, final_k)
