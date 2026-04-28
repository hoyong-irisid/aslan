"""
Gemini function-calling agent: model chooses when to search Qdrant vs answer from general knowledge.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.contacts import resolve_region
from app.partner_assets import resolve_figure_by_query, resolve_figure_candidates
from app.query_hints import looks_like_kb_or_product_query
from app.web_search import run_web_search
from config.settings import Settings, get_settings
from rag.retrieve import format_chunks_for_tool, retrieve_best_chunks, retrieve_prefetch_chunks
from rag.schemas import RagFilters

logger = logging.getLogger(__name__)

_TOOL_SEARCH_COMPANY: dict[str, Any] = {
    "name": "search_company_knowledge",
    "description": (
        "Search IRIS ID's public ingested knowledge base (website text, datasheets in Qdrant). "
        "Use for product specs, model numbers, dimensions, installation, company facts from official "
        "public materials. Prefer this when docs exist. If snippets are empty or clearly irrelevant to "
        "the user's exact question, follow up with web_search using an IRIS-focused query."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Concise search query; use the user's language if helpful.",
            },
        },
        "required": ["query"],
    },
}

_TOOL_WEB_SEARCH: dict[str, Any] = {
    "name": "web_search",
    "description": (
        "Search the public web. Use for news, competitors, and general facts. Also use after "
        "search_company_knowledge returns nothing useful for an IRIS model/overview question — "
        "query with e.g. \"IRIS ID IA-1000\" or product name so official site pages rank. "
        "Never invent precise specs (dimensions, certifications) not present in KB or snippet text. "
        "If pre-loaded company knowledge states a fact, do not contradict it with unrelated URLs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Web search query (short, keywords OK).",
            },
        },
        "required": ["query"],
    },
}

_TOOL_SEARCH_PARTNER: dict[str, Any] = {
    "name": "search_partner_knowledge",
    "description": (
        "Search IRIS ID's PARTNER-ONLY knowledge base (internal product manuals such as the IA-1000 "
        "user manual, version history). Available ONLY when the user has been verified as a partner. "
        "Prefer this tool over web_search and over search_company_knowledge for internal/manual-level "
        "questions about IRIS ID products. Do not reveal raw manual passages verbatim if they contain "
        "proprietary confidential data; summarize instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Concise search query; user's language is OK.",
            },
        },
        "required": ["query"],
    },
}


_TOOL_PARTNER_FIGURE: dict[str, Any] = {
    "name": "get_partner_figure",
    "description": (
        "Find a partner-only product figure/diagram image (from figures.json manifest). "
        "Use when user asks to 'show' or 'see' a diagram/image from manuals "
        "(e.g. IA-1000 rear view with installation plate)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Figure name or description to find.",
            },
        },
        "required": ["query"],
    },
}


def _tool_declarations(*, is_partner: bool) -> list[dict[str, Any]]:
    decls: list[dict[str, Any]] = [_TOOL_SEARCH_COMPANY, _TOOL_WEB_SEARCH]
    if is_partner:
        decls.append(_TOOL_SEARCH_PARTNER)
        decls.append(_TOOL_PARTNER_FIGURE)
    return decls


# Back-compat alias so external imports keep working.
TOOL_DECLARATIONS: list[dict[str, Any]] = _tool_declarations(is_partner=False)


def _agent_system_instruction(
    settings: Settings,
    region_country: str | None,
    *,
    kb_prefetch_block: str | None,
    is_partner: bool = False,
) -> str:
    r = resolve_region(region_country)
    sales = f"{r.sales.name} — {r.sales.email}, {r.sales.phone} ({r.label})"
    sup = f"{r.support.name} — {r.support.email}, {r.support.phone}"
    kb_section = ""
    if kb_prefetch_block:
        kb_section = f"""
--- Company knowledge (pre-loaded from ingested docs; authoritative for IRIS products & specs) ---
{kb_prefetch_block}
---
When this block directly answers the user's IRIS product/model/spec question: use only this block and the user message. Do not use web_search to contradict facts stated here.
If the block does not cover their exact question (wrong product, empty, or only partial): call `search_company_knowledge`. If that still returns nothing useful, call `web_search` with \"IRIS ID\" plus model name or keywords; summarize from official-looking results (e.g. irisid.com). Do not invent exact specs not supported by KB or snippets.
"""
    partner_section = ""
    if is_partner:
        partner_section = (
            "\nPARTNER MODE: This user is an authenticated IRIS ID partner.\n"
            "- For any IRIS product / model / spec / install / troubleshooting question (esp. IA-1000), "
            "call `search_partner_knowledge` FIRST. It covers internal manuals and history not on the public web.\n"
            "- If partner KB does not answer, fall back to `search_company_knowledge`, then `web_search`.\n"
            "- If user asks to show a manual image/diagram, call `get_partner_figure`.\n"
            "- You may quote concrete facts from the partner manual (steps, default values, warnings), but do not "
            "leak large verbatim sections or anything marked confidential; summarize and cite the source filename.\n"
            "- When a function response contains a line beginning with `IMAGE_URL:`, include that line verbatim in your final reply.\n"
        )
    else:
        partner_section = (
            "\nPUBLIC MODE: This user is NOT authenticated as a partner.\n"
            "- Do not use `search_partner_knowledge` (it is unavailable).\n"
            "- Answer only from public sources: `search_company_knowledge` and `web_search`.\n"
            "- If the user asks for internal/manual-level IRIS info you cannot give publicly, tell them "
            "that this information is available to verified IRIS ID partners and invite them to enter "
            "their partner access code to continue.\n"
        )
    return f"""You are IRIS ID's website assistant.
Rules:
- Match the user's language when you reply.
{kb_section}{partner_section}
- For IRIS models/products (e.g. \"what is IA-1000?\"): if partner mode, call `search_partner_knowledge` first; otherwise call `search_company_knowledge`. If snippets are empty or clearly off-topic, call `web_search` with an IRIS-focused query — public homepage/product pages are valid for high-level \"what is this\". Do not fabricate precise technical specs absent from KB or search results.
- If pre-loaded KB above is empty and the user needs IRIS facts: still call the appropriate search tool, then `web_search` if needed as above.
- Use `web_search` for clearly external topics too (news, non-IRIS general facts, competitors).
- For casual chat with no factual lookup, answer directly without tools.
- Never give specific prices or formal quotes. For pricing / purchase intent, tell the user to contact sales: {sales}
- For severe incidents or hands-on repair, suggest support: {sup}
Keep answers concise."""


def _gemini_request(settings: Settings, body: dict[str, Any]) -> dict[str, Any]:
    key = settings.google_api_key
    if not key:
        raise RuntimeError("GOOGLE_API_KEY is not set")
    model = settings.gemini_chat_model
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    with httpx.Client(timeout=settings.gemini_request_timeout_sec) as client:
        r = client.post(url, params={"key": key}, json=body)
        if r.status_code >= 400:
            detail = r.text.replace("\n", " ").strip()
            if len(detail) > 280:
                detail = detail[:280] + "..."
            raise RuntimeError(f"Gemini HTTP {r.status_code}: {detail}")
        return r.json()


def _parts_from_candidate(data: dict[str, Any]) -> list[dict[str, Any]]:
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError(f"Gemini returned no candidates: {data}")
    return cands[0].get("content", {}).get("parts") or []


def _execute_search_tool(query: str, settings: Settings, language_iso: str) -> str:
    base = RagFilters(language=language_iso)
    chunks = retrieve_best_chunks(query.strip() or query, settings, base)
    return format_chunks_for_tool(chunks)


def _execute_partner_search_tool(
    query: str,
    settings: Settings,
    language_iso: str,
) -> str:
    from rag.retrieve import retrieve_best_chunks_from

    base = RagFilters(language=language_iso, access="partner")
    chunks = retrieve_best_chunks_from(
        query.strip() or query,
        settings,
        collection_name=settings.qdrant_collection_partner,
        base=base,
    )
    return format_chunks_for_tool(chunks)


def run_gemini_agent(
    user_message: str,
    *,
    region_hint: str | None,
    language_iso: str,
    is_partner: bool = False,
    partner_token: str | None = None,
    chat_history: list[dict[str, str]] | None = None,
) -> str:
    settings = get_settings()
    kb_prefetch: str | None = None
    if looks_like_kb_or_product_query(user_message):
        try:
            pre = retrieve_prefetch_chunks(
                user_message,
                settings,
                RagFilters(language=language_iso),
            )
            if pre:
                kb_prefetch = format_chunks_for_tool(pre)
                logger.info("Gemini agent: attached %d prefetch KB chunks", len(pre))
        except Exception as exc:
            # Keep chat responsive when embeddings are temporarily rate-limited.
            logger.warning("Gemini agent: KB prefetch skipped due to error: %s", exc)

    system_text = _agent_system_instruction(
        settings,
        str(region_hint).upper() if region_hint else None,
        kb_prefetch_block=kb_prefetch,
        is_partner=is_partner,
    )
    contents: list[dict[str, Any]] = []
    for turn in chat_history or []:
        role = str(turn.get("role", "")).strip().lower()
        text = str(turn.get("text", "")).strip()
        if role not in ("user", "assistant") or not text:
            continue
        gm_role = "model" if role == "assistant" else "user"
        contents.append({"role": gm_role, "parts": [{"text": text}]})
    # Ensure the current user turn is always present as the final turn.
    contents.append({"role": "user", "parts": [{"text": user_message}]})
    last_text_reply = ""
    tool_decls = _tool_declarations(is_partner=is_partner)

    for round_i in range(settings.gemini_max_tool_rounds):
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_text}]},
            "contents": contents,
            "tools": [{"functionDeclarations": tool_decls}],
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        }
        data = _gemini_request(settings, body)
        parts = _parts_from_candidate(data)

        text_bits: list[str] = []
        calls: list[dict[str, Any]] = []
        for p in parts:
            if "text" in p:
                text_bits.append(p["text"])
            fc = p.get("functionCall")
            if fc:
                calls.append(fc)

        if not calls:
            return "".join(text_bits).strip() or "(no reply)"
        # Keep the latest model text in case we hit round limit.
        if text_bits:
            last_text_reply = "".join(text_bits).strip()

        # Append model turn (function calls)
        contents.append({"role": "model", "parts": parts})

        fr_parts: list[dict[str, Any]] = []
        for fc in calls:
            name = fc.get("name", "")
            args = fc.get("args") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if name == "search_company_knowledge":
                q = args.get("query") or user_message
                logger.info("Gemini tool search_company_knowledge query=%r", q)
                try:
                    payload = _execute_search_tool(str(q), settings, language_iso)
                except Exception as exc:
                    logger.warning("Gemini tool search_company_knowledge failed: %s", exc)
                    em = f"{exc}".lower()
                    if "index required but not found" in em or (
                        "bad request" in em and "keyword" in em and "filter" in em
                    ):
                        payload = (
                            "Qdrant payload indexes are missing (Qdrant Cloud requires keyword "
                            "indexes on filter fields). Ask the operator to run from repo root: "
                            "python -m rag.qdrant_indexes — then retry search_company_knowledge. "
                            "Until then you may use web_search for public IRIS information."
                        )
                    else:
                        payload = (
                            "Company knowledge search is temporarily unavailable "
                            "(embedding service rate-limited or Qdrant error). "
                            "Continue with web_search for public information, "
                            "or answer conservatively if web results are insufficient."
                        )
            elif name == "search_partner_knowledge":
                q = args.get("query") or user_message
                logger.info("Gemini tool search_partner_knowledge query=%r", q)
                if not is_partner:
                    payload = (
                        "Partner KB is not available for this user (not authenticated). "
                        "Ask the user to provide a valid partner access code first."
                    )
                else:
                    try:
                        payload = _execute_partner_search_tool(
                            str(q), settings, language_iso
                        )
                    except Exception as exc:
                        logger.warning("Gemini tool search_partner_knowledge failed: %s", exc)
                        em = f"{exc}".lower()
                        if "index required but not found" in em:
                            payload = (
                                "Partner Qdrant collection is missing payload indexes. "
                                "Operator: run `python -m rag.qdrant_indexes "
                                f"--collection {settings.qdrant_collection_partner}` "
                                "(or re-run partner ingest)."
                            )
                        else:
                            payload = (
                                "Partner KB search failed. "
                                "Fallback to search_company_knowledge or web_search if useful."
                            )
            elif name == "get_partner_figure":
                q = args.get("query") or user_message
                logger.info("Gemini tool get_partner_figure query=%r", q)
                if not is_partner or not partner_token:
                    payload = (
                        "Partner figure access is unavailable (not authenticated). "
                        "Ask user to verify partner code first."
                    )
                else:
                    fig = resolve_figure_by_query(str(q))
                    if fig is None:
                        cands = resolve_figure_candidates(str(q), limit=3)
                        if cands:
                            opts_human = "\n".join(
                                f"- {c.title} (id: {c.asset_id})" for c in cands
                            )
                            opts_machine = "\n".join(
                                f"FIGURE_OPTION: {c.asset_id}|{c.title}" for c in cands
                            )
                            payload = (
                                "I found multiple possible figures and need a more specific request. "
                                "Please choose one of these:\n"
                                f"{opts_human}\n"
                                f"{opts_machine}"
                            )
                        else:
                            payload = (
                                "No partner figure matched that request. "
                                "Try a more specific caption or model section title."
                            )
                    else:
                        url = f"/partner/asset/{fig.asset_id}?token={partner_token}"
                        payload = (
                            f"Figure: {fig.title}\n"
                            f"IMAGE_URL: {url}"
                        )
            elif name == "web_search":
                q = args.get("query") or user_message
                logger.info("Gemini tool web_search query=%r", q)
                payload = run_web_search(str(q), settings)
            else:
                payload = f"Unknown tool: {name}"
            fr_parts.append(
                {
                    "functionResponse": {
                        "name": name,
                        "response": {"snippets": payload},
                    }
                }
            )
        contents.append({"role": "user", "parts": fr_parts})

    logger.warning("Gemini agent exceeded max tool rounds")
    if last_text_reply:
        return last_text_reply
    return "I could not complete all tool steps in time. Please try a shorter question."
