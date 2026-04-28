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
    return re.sub(r"\s+", " ", (text or "").strip().lower())


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
    "ia1000",
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


def resolve_figure_candidates(query: str, limit: int = 5) -> list[FigureAsset]:
    assets = load_figure_assets()
    if not assets:
        return []
    ranked = sorted(assets, key=lambda a: _score_asset(query, a), reverse=True)
    # Keep only candidates with non-trivial score.
    keep = [a for a in ranked if _score_asset(query, a) >= 40]
    return keep[: max(1, limit)]


def resolve_figure_by_query(query: str) -> FigureAsset | None:
    cands = resolve_figure_candidates(query, limit=3)
    if not cands:
        return None
    best = cands[0]
    best_score = _score_asset(query, best)
    # Be conservative: avoid wrong image when confidence is low.
    if best_score < 80:
        return None
    if len(cands) >= 2:
        second = cands[1]
        # If top-2 are too close, treat as ambiguous.
        if best_score - _score_asset(query, second) < 20:
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
