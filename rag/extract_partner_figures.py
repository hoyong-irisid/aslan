"""
Extract embedded images from partner PDF manuals and auto-generate figures.json.

Default target:
  - docs root: PARTNER_DOCS_DIR or ../aslan-rag/partner_docs
  - output images: <docs root>/assets/
  - manifest: <docs root>/figures.json

Usage examples:
  python -m rag.extract_partner_figures
  python -m rag.extract_partner_figures --pdf "/path/to/iA1000_User_Manual.pdf"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from config.settings import resolve_partner_docs_path


def _guess_ext(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif"
    if data.startswith(b"RIFF") and b"WEBP" in data[:32]:
        return "webp"
    if data.startswith(b"\x00\x00\x00\x0cjP  \r\n\x87\n"):
        return "jp2"
    return "bin"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "figure"


def _pick_pdf(root: Path, override: str | None) -> Path:
    if override:
        p = Path(override).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if not p.is_file():
            raise SystemExit(f"PDF not found: {p}")
        return p
    pdfs = sorted(root.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(
            f"No PDF found in {root}. Pass --pdf explicitly or place one there first."
        )
    return pdfs[0]


def _extract_caption_lines(page_text: str) -> list[str]:
    lines = [ln.strip() for ln in (page_text or "").splitlines() if ln.strip()]
    out: list[str] = []
    for ln in lines:
        # Example matches:
        # - Figure 4.3 iA1000 Rear View with Installation Plate
        # - 4.3 iA1000 - Rear View with Installation Plate
        if re.search(r"\bfigure\b", ln, flags=re.IGNORECASE):
            out.append(ln)
            continue
        if re.match(r"^\d+(\.\d+){1,3}\s+", ln):
            out.append(ln)
    return out[:12]


def _uniq_keep_order(items: list[str], limit: int = 12) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        s = (it or "").strip()
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= max(1, limit):
            break
    return out


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"figures": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"figures": []}
    if not isinstance(data, dict):
        return {"figures": []}
    figs = data.get("figures")
    if not isinstance(figs, list):
        data["figures"] = []
    return data


def _upsert_figure_rows(manifest: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    current = manifest.get("figures") or []
    by_id: dict[str, dict[str, Any]] = {}
    for row in current:
        if isinstance(row, dict) and row.get("id"):
            by_id[str(row["id"])] = row
    for row in rows:
        by_id[row["id"]] = row
    manifest["figures"] = list(by_id.values())


def _prune_missing_files(manifest: dict[str, Any], root: Path) -> int:
    rows = manifest.get("figures")
    if not isinstance(rows, list):
        manifest["figures"] = []
        return 0
    kept: list[dict[str, Any]] = []
    removed = 0
    root_resolved = root.resolve()
    for row in rows:
        if not isinstance(row, dict):
            removed += 1
            continue
        file_rel = str(row.get("file", "")).strip()
        if not file_rel:
            removed += 1
            continue
        p = (root_resolved / file_rel).resolve()
        try:
            p.relative_to(root_resolved)
        except Exception:
            removed += 1
            continue
        if not p.is_file():
            removed += 1
            continue
        kept.append(row)
    manifest["figures"] = kept
    return removed


def _image_size(blob: bytes) -> tuple[int, int]:
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(blob)) as im:
        return int(im.width), int(im.height)


def _reject_reason(
    *,
    blob: bytes,
    width: int,
    height: int,
    min_side_px: int,
    min_area_px: int,
    max_aspect_ratio: float,
) -> str | None:
    area = width * height
    short_side = min(width, height)
    long_side = max(width, height)
    aspect = (long_side / short_side) if short_side > 0 else 999.0
    if len(blob) < 1500:
        return "tiny_bytes"
    if short_side < min_side_px:
        return "small_side"
    if area < min_area_px:
        return "small_area"
    if aspect > max_aspect_ratio:
        return "extreme_aspect"
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pdf",
        type=str,
        default=None,
        help="PDF path to process. Default: first *.pdf in partner docs dir.",
    )
    parser.add_argument(
        "--partner-docs-dir",
        type=str,
        default=None,
        help="Override partner docs root (default from PARTNER_DOCS_DIR / config).",
    )
    parser.add_argument(
        "--min-side-px",
        type=int,
        default=96,
        help="Reject images when min(width,height) is below this (default: 96).",
    )
    parser.add_argument(
        "--min-area-px",
        type=int,
        default=24000,
        help="Reject images when width*height is below this (default: 24000).",
    )
    parser.add_argument(
        "--max-aspect-ratio",
        type=float,
        default=8.0,
        help="Reject images with extreme aspect ratio (default: 8.0).",
    )
    parser.add_argument(
        "--clean-generated-assets",
        action="store_true",
        help="Delete prior auto-generated files for this PDF prefix before writing new ones.",
    )
    parser.add_argument(
        "--no-prune-missing",
        action="store_true",
        help="Do not remove manifest rows whose file paths are missing.",
    )
    args = parser.parse_args()

    root = (
        Path(args.partner_docs_dir).expanduser().resolve()
        if args.partner_docs_dir
        else resolve_partner_docs_path()
    )
    root.mkdir(parents=True, exist_ok=True)
    pdf_path = _pick_pdf(root, args.pdf)
    assets_dir = root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "figures.json"

    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("Install dependency first: pip install pypdf") from exc

    reader = PdfReader(str(pdf_path))
    pdf_stem = _slug(pdf_path.stem)
    new_rows: list[dict[str, Any]] = []
    kept_hashes: set[str] = set()
    extracted = 0
    rejected = 0
    reject_stats: dict[str, int] = {}

    if args.clean_generated_assets:
        for old in assets_dir.glob(f"{pdf_stem}-p*-img*.*"):
            old.unlink(missing_ok=True)

    for page_idx, page in enumerate(reader.pages, start=1):
        captions = _extract_caption_lines(page.extract_text() or "")
        images = list(page.images)
        if not images:
            continue
        for img_idx, img in enumerate(images, start=1):
            blob = img.data
            sig = hashlib.sha1(blob).hexdigest()
            if sig in kept_hashes:
                rejected += 1
                reject_stats["duplicate_blob"] = reject_stats.get("duplicate_blob", 0) + 1
                continue
            ext = _guess_ext(blob)
            if ext == "bin":
                # Unsupported image format signature. Still write a binary file to inspect manually.
                ext = "bin"
            try:
                width, height = _image_size(blob)
            except Exception:
                rejected += 1
                reject_stats["unreadable_image"] = reject_stats.get("unreadable_image", 0) + 1
                continue
            reason = _reject_reason(
                blob=blob,
                width=width,
                height=height,
                min_side_px=max(1, args.min_side_px),
                min_area_px=max(1, args.min_area_px),
                max_aspect_ratio=max(1.0, float(args.max_aspect_ratio)),
            )
            if reason:
                rejected += 1
                reject_stats[reason] = reject_stats.get(reason, 0) + 1
                continue
            img_name = f"{pdf_stem}-p{page_idx:03d}-img{img_idx:02d}.{ext}"
            out_path = assets_dir / img_name
            out_path.write_bytes(blob)
            kept_hashes.add(sig)

            cap = captions[min(img_idx - 1, len(captions) - 1)] if captions else ""
            title = cap or f"{pdf_path.stem} page {page_idx} image {img_idx}"
            asset_id = _slug(f"{pdf_stem}-{title}")[:90]
            aliases = [
                f"{pdf_path.stem} page {page_idx}",
                f"page {page_idx} image {img_idx}",
            ]
            if cap:
                aliases.append(cap)
            # Add page-level caption cues for better retrieval of auto-extracted images.
            aliases.extend(captions[:8])
            aliases = _uniq_keep_order(aliases, limit=12)
            new_rows.append(
                {
                    "id": asset_id,
                    "title": title,
                    "file": f"assets/{img_name}",
                    "aliases": aliases,
                    "meta": {
                        "page": page_idx,
                        "img_index": img_idx,
                        "width": width,
                        "height": height,
                        "bytes": len(blob),
                        "source_pdf": pdf_path.name,
                    },
                }
            )
            extracted += 1

    manifest = _load_manifest(manifest_path)
    _upsert_figure_rows(manifest, new_rows)
    pruned = 0
    if not args.no_prune_missing:
        pruned = _prune_missing_files(manifest, root)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Partner docs root: {root}")
    print(f"PDF: {pdf_path.name}")
    print(f"Extracted images (kept): {extracted}")
    print(f"Rejected images: {rejected}")
    if reject_stats:
        print(f"Reject breakdown: {reject_stats}")
    if pruned:
        print(f"Pruned stale manifest rows: {pruned}")
    print(f"Assets dir: {assets_dir}")
    print(f"Updated manifest: {manifest_path}")


if __name__ == "__main__":
    main()
