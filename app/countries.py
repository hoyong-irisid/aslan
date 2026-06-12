"""ISO 3166-1 country names for partner signup."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.contacts import load_contacts

_US_NAME = "United States"


@lru_cache
def _country_rows() -> list[dict[str, str]]:
    path = Path(__file__).resolve().parents[1] / "config" / "countries.json"
    if not path.is_file():
        return [{"iso": "US", "name": _US_NAME}]
    rows = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        iso = str(row.get("iso", "")).strip().upper()
        name = str(row.get("name", "")).strip()
        if len(iso) == 2 and name:
            out.append({"iso": iso, "name": name})
    return out


@lru_cache
def _name_to_iso() -> dict[str, str]:
    return {r["name"].casefold(): r["iso"] for r in _country_rows()}


def list_countries_for_select() -> list[dict[str, str]]:
    """All countries for dropdown; United States first, then A–Z."""
    rows = _country_rows()
    us = next((r for r in rows if r["iso"] == "US"), None)
    rest = sorted(
        (r for r in rows if r["iso"] != "US"),
        key=lambda r: r["name"].casefold(),
    )
    if us:
        return [us, *rest]
    return rest


def resolve_country_name(name: str) -> tuple[str, str]:
    """Validate country name; return (iso2, canonical name)."""
    key = (name or "").strip().casefold()
    if not key:
        raise ValueError("Country is required")
    iso = _name_to_iso().get(key)
    if not iso:
        raise ValueError("Unknown country")
    canonical = next(r["name"] for r in _country_rows() if r["iso"] == iso)
    return iso, canonical


def country_name_for_iso(iso2: str | None) -> str | None:
    cc = (iso2 or "").strip().upper()
    if len(cc) != 2:
        return None
    for row in _country_rows():
        if row["iso"] == cc:
            return row["name"]
    return None


def region_key_for_country_iso(iso2: str) -> str:
    try:
        contacts = load_contacts()
    except RuntimeError:
        return "north_america"
    cc = (iso2 or "").strip().upper()
    for key, region in contacts.regions.items():
        if cc in region.countries:
            return key
    return contacts.default_region
