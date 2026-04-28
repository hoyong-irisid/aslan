"""
Ingest text files (.md/.txt/.mdx) and PDFs into Qdrant.

Usage (public KB, default collection):
  python -m rag.ingest /path/to/docs --product iA1000 --language en

Usage (partner-only KB, separate collection — default dir is outside git):
  python -m rag.ingest --partner --product iA1000
  # reads from PARTNER_DOCS_DIR or ../aslan-rag/partner_docs next to this repo

  # Or pass an explicit folder:
  python -m rag.ingest /path/to/partner_docs --partner --product iA1000

Partner docs should live under ../aslan-rag/partner_docs (sibling of aslan/) so they are not committed.
"""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

import tiktoken
from qdrant_client import QdrantClient
from qdrant_client.http import models as rest

from config.settings import get_settings, resolve_partner_docs_path
from rag.embeddings import embed_texts
from rag.qdrant_indexes import ensure_filter_payload_indexes
from rag.query import get_client


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
    for ext in (".md", ".txt", ".mdx", ".pdf"):
        paths.extend(root.rglob(f"*{ext}"))
    return sorted(paths)


def _read_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "pypdf is required to ingest PDF files. Install with: pip install pypdf"
        ) from exc
    reader = PdfReader(str(path))
    pieces: list[str] = []
    for page in reader.pages:
        try:
            pieces.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n\n".join(pieces).strip()


def read_source_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return _read_pdf_text(path)
    return path.read_text(encoding="utf-8", errors="ignore")


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
    # Qdrant Cloud requires keyword indexes on payload keys used in query filters.
    ensure_filter_payload_indexes(client, name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source_dir",
        type=Path,
        nargs="?",
        default=None,
        help="Folder with .md/.txt/.pdf. Omit with --partner to use PARTNER_DOCS_DIR / default aslan-rag path.",
    )
    parser.add_argument("--prefix", type=str, default="", help="Metadata source prefix")
    parser.add_argument("--product", type=str, default=None)
    parser.add_argument("--language", type=str, default="en")
    parser.add_argument("--doc-type", type=str, default="manual")
    parser.add_argument("--department", type=str, default="support")
    parser.add_argument("--version", type=str, default=None)
    parser.add_argument("--access", type=str, default="internal")
    parser.add_argument(
        "--collection",
        type=str,
        default=None,
        help="Target Qdrant collection (default: from settings.qdrant_collection).",
    )
    parser.add_argument(
        "--partner",
        action="store_true",
        help="Shortcut: target settings.qdrant_collection_partner and set --access partner.",
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.source_dir is None:
        if args.partner:
            args.source_dir = resolve_partner_docs_path(settings)
        else:
            parser.error(
                "source_dir is required unless you pass --partner "
                "(then the default is PARTNER_DOCS_DIR or ../aslan-rag/partner_docs)"
            )

    if args.partner:
        collection_name = args.collection or settings.qdrant_collection_partner
        access = args.access if args.access != "internal" else "partner"
    else:
        collection_name = args.collection or settings.qdrant_collection
        access = args.access

    src = args.source_dir.resolve()
    print(f"Ingesting from: {src}")
    files = iter_source_files(src)
    if not files:
        raise SystemExit("No .md/.txt/.mdx/.pdf files found under " + str(src))

    client = get_client()
    probe = embed_texts(["dimension probe"])
    dim = len(probe[0])
    ensure_collection(client, collection_name, dim)

    points: list[rest.PointStruct] = []
    for path in files:
        rel = path.relative_to(src).as_posix()
        source = f"{args.prefix}/{rel}" if args.prefix else rel
        raw = read_source_text(path)
        if not raw.strip():
            print(f"  (skipped, empty text) {rel}")
            continue
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
                "access": access,
            }
            points.append(
                rest.PointStruct(
                    id=stable_id(source, i),
                    vector=vec,
                    payload=payload,
                )
            )

    if points:
        client.upsert(collection_name=collection_name, points=points)
    print(f"Upserted {len(points)} chunks into {collection_name} (access={access})")


if __name__ == "__main__":
    main()
