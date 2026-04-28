from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.settings import resolve_partner_docs_path


@dataclass
class FigureAsset:
    asset_id: str
    title: str
    file: str
    aliases: list[str]


def _manifest_path() -> Path:
    return resolve_partner_docs_path() / "figures.json"


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.replace("ia-1000", "ia1000")
    t = re.sub(r"\s+", " ", t)
    return t


_STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "for",
    "with",
    "and",
    "to",
    "in",
    "on",
    "view",
    "page",
    "image",
    "manual",
    "user",
    "section",
    "figure",
    "iris",
    "id",
}


def _tokens(text: str) -> set[str]:
    parts = re.findall(r"[a-z0-9]+", _normalize(text))
    return {p for p in parts if p and p not in _STOPWORDS and len(p) >= 2}


def _is_generic_title(title: str) -> bool:
    t = _normalize(title)
    return bool(re.match(r".*\bpage\s+\d+\s+image\s+\d+\b.*", t))


def load_figure_assets() -> list[FigureAsset]:
    """
    Read partner figure manifest.

    Accepted schema:
      {"figures":[{"id":"...", "title":"...", "file":"...", "aliases":[...]}]}
    """
    path = _manifest_path()
    if not path.is_file():
        return []
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = raw.get("figures") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return []
    root = resolve_partner_docs_path().resolve()
    out: list[FigureAsset] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        asset_id = str(row.get("id", "")).strip()
        title = str(row.get("title", "")).strip()
        file = str(row.get("file", "")).strip()
        aliases_raw = row.get("aliases") or []
        aliases = [str(a).strip() for a in aliases_raw if str(a).strip()]
        if not (asset_id and title and file):
            continue
        # If manifest is stale (file deleted/moved), skip it from retrieval candidates.
        p = (root / file).resolve()
        try:
            p.relative_to(root)
        except Exception:
            continue
        if not p.is_file():
            continue
        out.append(FigureAsset(asset_id=asset_id, title=title, file=file, aliases=aliases))
    return out


def _score_asset(query: str, asset: FigureAsset) -> int:
    q = _normalize(query)
    if not q:
        return 0
    keys = [_normalize(asset.title), *[_normalize(x) for x in asset.aliases], _normalize(asset.asset_id)]
    q_tokens = _tokens(q)
    s = 0
    for k in keys:
        if not k:
            continue
        if k == q:
            s += 200
        elif k in q:
            s += 80
        elif q in k:
            s += 60
        k_tokens = _tokens(k)
        overlap = len(q_tokens & k_tokens) if q_tokens and k_tokens else 0
        s += overlap * 20
        if overlap >= 3:
            s += 40
    # Penalize generic auto titles so meaningful captions win.
    if _is_generic_title(asset.title):
        s -= 30
    return s


def _expand_query_variants(query: str) -> list[str]:
    q = _normalize(query)
    if not q:
        return []
    variants = [q]

    # Common follow-up phrasing (EN + KO) for "show real photo/image".
    replaced = q
    replaced = re.sub(r"\breal\s+photo(s)?\b", "front view photo", replaced)
    replaced = re.sub(r"\breal\s+image(s)?\b", "front view photo", replaced)
    replaced = re.sub(r"\bactual\s+photo(s)?\b", "front view photo", replaced)
    replaced = re.sub(r"\bphoto(s)?\b", "image", replaced)
    replaced = replaced.replace("진짜 사진", "실물 사진")
    replaced = replaced.replace("실사", "실물 사진")
    replaced = replaced.replace("실물", "front view")
    if replaced != q:
        variants.append(replaced)

    # If request is too short/ambiguous, bias toward common product shot labels.
    short_q = re.sub(r"[^a-z0-9가-힣 ]+", " ", q).strip()
    if len(short_q) <= 16:
        variants.append(f"{q} front view")
        variants.append(f"{q} camera unit")
        variants.append(f"{q} rear view")

    # De-duplicate while preserving order.
    out: list[str] = []
    seen: set[str] = set()
    for v in variants:
        vv = _normalize(v)
        if not vv or vv in seen:
            continue
        seen.add(vv)
        out.append(vv)
    return out


def resolve_figure_candidates(query: str, limit: int = 5) -> list[FigureAsset]:
    assets = load_figure_assets()
    if not assets:
        return []
    variants = _expand_query_variants(query) or [_normalize(query)]
    ranked = sorted(
        assets,
        key=lambda a: max(_score_asset(v, a) for v in variants),
        reverse=True,
    )
    # Keep candidates with modest score so auto-extracted figures still surface.
    keep = [a for a in ranked if max(_score_asset(v, a) for v in variants) >= 20]
    return keep[: max(1, limit)]


def resolve_figure_by_query(query: str) -> FigureAsset | None:
    cands = resolve_figure_candidates(query, limit=3)
    if not cands:
        return None
    best = cands[0]
    variants = _expand_query_variants(query) or [_normalize(query)]
    best_score = max(_score_asset(v, best) for v in variants)
    # Conservative, but not too strict: extracted assets often have weak captions.
    if best_score < 55:
        return None
    if len(cands) >= 2:
        second = cands[1]
        # If top-2 are too close, treat as ambiguous.
        second_score = max(_score_asset(v, second) for v in variants)
        if best_score - second_score < 12:
            return None
    return best


def resolve_asset_file(asset_id: str) -> Path | None:
    root = resolve_partner_docs_path()
    for a in load_figure_assets():
        if a.asset_id != asset_id:
            continue
        p = (root / a.file).resolve()
        try:
            p.relative_to(root)
        except Exception:
            return None
        if not p.is_file():
            return None
        return p
    return None
