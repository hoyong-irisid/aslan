"""
Ingest text files from a directory into Qdrant.

Usage:
  python -m rag.ingest /path/to/docs --prefix manuals

Keep corpus outside the repo; this script only reads paths you pass.
"""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

import tiktoken
from qdrant_client import QdrantClient
from qdrant_client.http import models as rest

from config.settings import get_settings
from rag.embeddings import embed_texts


def _token_len(enc: tiktoken.Encoding, text: str) -> int:
    return len(enc.encode(text))


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    if not tokens:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        piece = enc.decode(tokens[start:end])
        chunks.append(piece.strip())
        if end >= len(tokens):
            break
        start = max(0, end - overlap)
    return [c for c in chunks if c]


def iter_source_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for ext in (".md", ".txt", ".mdx"):
        paths.extend(root.rglob(f"*{ext}"))
    return sorted(paths)


def stable_id(source: str, chunk_index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}:{chunk_index}"))


def ensure_collection(client: QdrantClient, name: str, dim: int) -> None:
    exists = False
    try:
        client.get_collection(name)
        exists = True
    except Exception:
        exists = False
    if not exists:
        client.create_collection(
            collection_name=name,
            vectors_config=rest.VectorParams(size=dim, distance=rest.Distance.COSINE),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--prefix", type=str, default="", help="Metadata source prefix")
    parser.add_argument("--product", type=str, default=None)
    parser.add_argument("--language", type=str, default="en")
    parser.add_argument("--doc-type", type=str, default="manual")
    parser.add_argument("--department", type=str, default="support")
    parser.add_argument("--version", type=str, default=None)
    parser.add_argument("--access", type=str, default="internal")
    args = parser.parse_args()

    settings = get_settings()
    files = iter_source_files(args.source_dir)
    if not files:
        raise SystemExit("No .md/.txt files found.")

    client = QdrantClient(url=settings.qdrant_url)
    # Probe embedding dimension
    probe = embed_texts(["dimension probe"])
    dim = len(probe[0])
    ensure_collection(client, settings.qdrant_collection, dim)

    points: list[rest.PointStruct] = []
    for path in files:
        rel = path.relative_to(args.source_dir).as_posix()
        source = f"{args.prefix}/{rel}" if args.prefix else rel
        raw = path.read_text(encoding="utf-8", errors="ignore")
        parts = chunk_text(
            raw,
            settings.chunk_size_tokens,
            settings.chunk_overlap_tokens,
        )
        vectors = embed_texts(parts) if parts else []
        for i, (chunk, vec) in enumerate(zip(parts, vectors, strict=True)):
            payload = {
                "text": chunk,
                "source": source,
                "doc_type": args.doc_type,
                "product": args.product,
                "language": args.language,
                "department": args.department,
                "version": args.version,
                "access": args.access,
            }
            points.append(
                rest.PointStruct(
                    id=stable_id(source, i),
                    vector=vec,
                    payload=payload,
                )
            )

    if points:
        client.upsert(collection_name=settings.qdrant_collection, points=points)
    print(f"Upserted {len(points)} chunks into {settings.qdrant_collection}")


if __name__ == "__main__":
    main()
