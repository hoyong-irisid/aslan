from typing import Any

from pydantic import BaseModel, Field


class ChunkMetadata(BaseModel):
    source: str
    doc_type: str | None = None
    product: str | None = None
    language: str | None = None
    department: str | None = None
    version: str | None = None
    access: str | None = None


class RetrievedChunk(BaseModel):
    text: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class RagFilters(BaseModel):
    product: str | None = None
    language: str | None = None
    doc_type: str | None = None
    department: str | None = None
    version: str | None = None
    access: str | None = None
