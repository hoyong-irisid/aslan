"""Heuristics for when to force / prefetch knowledge-base retrieval (avoid web-first wrong answers)."""

from __future__ import annotations

import re


def looks_like_kb_or_product_query(message: str) -> bool:
    """True if the user likely asks about company products, specs, or IRIS-specific facts."""
    m = message.lower()
    if any(
        k in m
        for k in (
            "spec",
            "specs",
            "dimension",
            "datasheet",
            "manual",
            "launch date",
            "product",
            "model",
            "iris id",
        )
    ):
        return True
    if any(
        k in message
        for k in (
            "제품",
            "모델",
            "사양",
            "스펙",
            "규격",
            "수치",
            "매뉴얼",
        )
    ):
        return True
    if re.search(r"\bia-?\s*\d+", m, re.IGNORECASE):
        return True
    if re.search(r"\b(i|d)[- ]?[a-z]?\d{3,}\b", m, re.IGNORECASE):
        return True
    if "iris" in m and len(message) < 200:
        return True
    return False
