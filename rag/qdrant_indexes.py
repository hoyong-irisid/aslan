"""
Ensure Qdrant payload indexes exist for fields used in rag/filters.py.

Qdrant Cloud rejects filtered queries when a Match filter references a key
without a keyword (or compatible) index — error like:
  Index required but not found for \"product\" ... types: [keyword]
"""

from __future__ import annotations

import argparse

from qdrant_client import QdrantClient
from qdrant_client.http import models as rest

from rag.query import get_client

# Keys used by qdrant_filter() — keep in sync with rag/filters.py
FILTER_PAYLOAD_KEYWORD_FIELDS: tuple[str, ...] = (
    "product",
    "language",
    "doc_type",
    "department",
    "version",
    "access",
)


def ensure_filter_payload_indexes(client: QdrantClient, collection_name: str) -> None:
    info = client.get_collection(collection_name)
    existing = info.payload_schema or {}
    for field in FILTER_PAYLOAD_KEYWORD_FIELDS:
        if field in existing:
            continue
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field,
            field_schema=rest.PayloadSchemaType.KEYWORD,
            wait=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create missing keyword payload indexes for RAG filters (Qdrant Cloud)."
    )
    parser.add_argument(
        "--collection",
        type=str,
        default=None,
        help="Override QDRANT_COLLECTION (default: from settings)",
    )
    args = parser.parse_args()

    from config.settings import get_settings

    settings = get_settings()
    name = args.collection or settings.qdrant_collection
    client = get_client()
    ensure_filter_payload_indexes(client, name)
    print(f"Payload indexes OK for collection {name!r} (fields: {FILTER_PAYLOAD_KEYWORD_FIELDS}).")


if __name__ == "__main__":
    main()
