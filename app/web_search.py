"""
Optional web search for the Gemini agent (Tavily, Serper, or Google Programmable Search).
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from config.settings import Settings

logger = logging.getLogger(__name__)


def _format_results(items: list[dict[str, str]], max_chars: int = 12000) -> str:
    lines: list[str] = []
    for i, it in enumerate(items, start=1):
        title = it.get("title", "").strip()
        url = it.get("url", "").strip()
        snippet = it.get("snippet", "").strip()
        lines.append(f"{i}. {title}\n   URL: {url}\n   {snippet}")
    text = "\n\n".join(lines) if lines else "(no results)"
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[truncated]"
    return text


def _search_tavily(query: str, settings: Settings) -> str:
    key = settings.tavily_api_key
    if not key:
        raise RuntimeError("TAVILY_API_KEY not set")
    body = {
        "api_key": key,
        "query": query,
        "max_results": settings.web_search_max_results,
        "search_depth": "basic",
    }
    with httpx.Client(timeout=settings.web_search_timeout_sec) as client:
        r = client.post("https://api.tavily.com/search", json=body)
        r.raise_for_status()
        data = r.json()
    items: list[dict[str, str]] = []
    for row in data.get("results") or []:
        items.append(
            {
                "title": str(row.get("title", "")),
                "url": str(row.get("url", "")),
                "snippet": str(row.get("content", "") or row.get("snippet", "")),
            }
        )
    return _format_results(items)


def _search_serper(query: str, settings: Settings) -> str:
    key = settings.serper_api_key
    if not key:
        raise RuntimeError("SERPER_API_KEY not set")
    body = {"q": query, "num": settings.web_search_max_results}
    with httpx.Client(timeout=settings.web_search_timeout_sec) as client:
        r = client.post(
            "https://google.serper.dev/search",
            json=body,
            headers={"X-API-KEY": key},
        )
        r.raise_for_status()
        data = r.json()
    items: list[dict[str, str]] = []
    for row in data.get("organic") or []:
        items.append(
            {
                "title": str(row.get("title", "")),
                "url": str(row.get("link", "")),
                "snippet": str(row.get("snippet", "")),
            }
        )
    return _format_results(items)


def _search_google_cse(query: str, settings: Settings) -> str:
    key = settings.google_cse_api_key
    cx = settings.google_cse_id
    if not key or not cx:
        raise RuntimeError("GOOGLE_CSE_API_KEY and GOOGLE_CSE_ID must be set")
    params: dict[str, Any] = {
        "key": key,
        "cx": cx,
        "q": query,
        "num": min(settings.web_search_max_results, 10),
    }
    url = f"https://www.googleapis.com/customsearch/v1?{urlencode(params)}"
    with httpx.Client(timeout=settings.web_search_timeout_sec) as client:
        r = client.get(url)
        r.raise_for_status()
        data = r.json()
    if "error" in data:
        er = data["error"]
        msg = er.get("message", str(er))
        code = er.get("code", "")
        return f"Google Custom Search API error ({code}): {msg}"
    items: list[dict[str, str]] = []
    for row in data.get("items") or []:
        items.append(
            {
                "title": str(row.get("title", "")),
                "url": str(row.get("link", "")),
                "snippet": str(row.get("snippet", "")),
            }
        )
    return _format_results(items)


def web_search_configured(settings: Settings) -> bool:
    p = (settings.web_search_provider or "auto").lower()
    if p == "none":
        return False
    if p == "tavily":
        return bool(settings.tavily_api_key)
    if p == "serper":
        return bool(settings.serper_api_key)
    if p in ("google_cse", "google"):
        return bool(settings.google_cse_api_key and settings.google_cse_id)
    # auto
    return bool(
        settings.tavily_api_key
        or settings.serper_api_key
        or (settings.google_cse_api_key and settings.google_cse_id)
    )


def run_web_search(query: str, settings: Settings) -> str:
    q = (query or "").strip()
    if not q:
        return "(empty query)"
    provider = (settings.web_search_provider or "auto").lower()

    try:
        if provider == "none":
            return (
                "Web search is disabled (WEB_SEARCH_PROVIDER=none). "
                "Answer from general knowledge or company tools only."
            )
        if provider == "tavily":
            return _search_tavily(q, settings)
        if provider == "serper":
            return _search_serper(q, settings)
        if provider in ("google_cse", "google"):
            return _search_google_cse(q, settings)
        # auto: prefer Google CSE (same index as google.com), then Tavily, Serper
        if settings.google_cse_api_key and settings.google_cse_id:
            return _search_google_cse(q, settings)
        if settings.tavily_api_key:
            return _search_tavily(q, settings)
        if settings.serper_api_key:
            return _search_serper(q, settings)
        return (
            "Web search is not configured. Recommended: GOOGLE_CSE_API_KEY + GOOGLE_CSE_ID "
            "(Google Programmable Search), or TAVILY_API_KEY / SERPER_API_KEY. See .env.example."
        )
    except httpx.HTTPStatusError as e:
        detail = e.response.text.replace("\n", " ")[:400]
        logger.warning("web_search HTTP error: %s", detail)
        return f"Web search failed (HTTP {e.response.status_code}): {detail}"
    except Exception as e:
        logger.exception("web_search error: %s", e)
        return f"Web search error: {e!s}"

