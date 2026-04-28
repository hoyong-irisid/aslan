import logging
import re
from dataclasses import dataclass

from langdetect import detect

from app.agent_gemini import run_gemini_agent
from app.contacts import resolve_region
from app.faq import match_faq
from app.llm import answer_with_rag, generate, route_message
from app.partner import (
    extract_valid_code,
    is_partner_session,
    issue_partner_token,
    message_asks_about_partner,
    partner_enabled,
)
from app.query_hints import looks_like_kb_or_product_query
from config.settings import get_settings
from rag.retrieve import retrieve_best_chunks
from rag.schemas import RagFilters


@dataclass
class ChatResult:
    reply: str
    partner_authenticated: bool = False
    partner_token: str | None = None

logger = logging.getLogger(__name__)

_FALLBACK_ROUTE: dict = {
    "intent": "other",
    "sentiment": "neutral",
    "needs_rag": False,
    "needs_sales_handoff": False,
    "needs_support_handoff": False,
    "product_guess": None,
    "country_guess": None,
}


def detect_language_iso(text: str) -> str:
    try:
        return detect(text)[:2]
    except Exception:
        return "en"


_SALES_TRIG = re.compile(
    r"\b(price|pricing|quote|quotation|cost|purchase|buy|order|how\s+much)\b",
    re.IGNORECASE,
)


def format_handoff_sales(region_country: str | None) -> str:
    r = resolve_region(region_country)
    p = r.sales
    return (
        f"For pricing and quotes, please contact our {r.label} team: "
        f"{p.name} — {p.email}, {p.phone}."
    )


def format_handoff_support(region_country: str | None) -> str:
    r = resolve_region(region_country)
    p = r.support
    return (
        f"For hands-on technical help, please contact support: {p.name} — "
        f"{p.email}, {p.phone}."
    )


def _config_error_reply() -> str:
    return (
        "The chat AI is not configured. Add OPENAI_API_KEY and set LLM_PROVIDER=openai, "
        "or add GOOGLE_API_KEY and set LLM_PROVIDER=gemini in your .env file, then restart the server."
    )


def redact_secrets(text: str) -> str:
    """Strip API keys from error strings (e.g. httpx URLs with ?key=...)."""
    text = re.sub(r"([?&])key=[^&\s'\"]+", r"\1key=***redacted***", text, flags=re.IGNORECASE)
    text = re.sub(r"Bearer\s+[A-Za-z0-9\-_.]+", "Bearer ***redacted***", text)
    return text


def _qdrant_connection_hint(exc: Exception) -> str:
    s = f"{type(exc).__name__}: {exc}".lower()
    if "connection refused" in s or "errno 61" in s or "10061" in s:
        return (
            "\n\nLikely cause: Qdrant is not running (nothing listening on QDRANT_URL). "
            "From the repo root run: docker compose up -d\n"
            "Then ingest documents: python -m rag.ingest /path/to/docs --language en ..."
        )
    if "index required but not found" in s or (
        "bad request" in s and "keyword" in s and "filter" in s
    ):
        return (
            "\n\nQdrant Cloud needs keyword indexes on payload fields used in filters "
            "(product, language, doc_type, …). From the repo root run:\n"
            "  python -m rag.qdrant_indexes\n"
            "Or run ingest again — it creates these indexes automatically."
        )
    return ""


def _diagnostic_from_exc(exc: Exception) -> str:
    msg = str(exc).strip()
    if not msg:
        return exc.__class__.__name__
    msg = " ".join(msg.split())
    if len(msg) > 220:
        msg = msg[:220] + "..."
    return redact_secrets(f"{exc.__class__.__name__}: {msg}")


def _llm_failure_reply(exc: Exception) -> str:
    """User-facing text: not always 'not configured' — e.g. Gemini 429 is quota / rate limit."""
    detail = _diagnostic_from_exc(exc)
    low = f"{exc!s}".lower()
    if (
        "429" in str(exc)
        or "too many requests" in low
        or "resource exhausted" in low
        or "quota" in low
        or "rate limit" in low
    ):
        return (
            "The Gemini API returned rate limit / quota (HTTP 429). This is not an .env typo — "
            "your key is probably valid, but the free tier or per-minute limit was hit.\n\n"
            "Try: wait 1–2 minutes; reduce how often you click Send; in Google AI Studio check "
            "usage/limits; or set GEMINI_CHAT_MODEL to another model (e.g. gemini-2.5-flash) "
            "and restart.\n\n"
            "Also confirm GOOGLE_API_KEY is from https://aistudio.google.com — keys there usually "
            "start with AIza. If yours was pasted into logs or chat, rotate it.\n\n"
            f"Detail: {detail}"
        )
    if (
        "404" in str(exc)
        or ("not found" in low and "model" in low)
        or "is not supported for generatecontent" in low
    ):
        return (
            "Gemini returned HTTP 404 for this model id. Common cases:\n"
            "• New AI Studio keys: gemini-2.0-flash is often disabled — use a newer id from the "
            "Models list (e.g. GEMINI_CHAT_MODEL=gemini-2.5-flash).\n"
            "• Wrong or retired name: open AI Studio → Models and copy the exact generateContent id.\n"
            "• Bare gemini-1.5-flash often fails; prefer a versioned id (e.g. gemini-1.5-flash-002) if listed.\n\n"
            f"Detail: {detail}"
        )
    if "401" in str(exc) or "403" in str(exc) or ("api key" in low and "invalid" in low):
        return (
            "The API key was rejected (HTTP 401/403) or lacks access to the model.\n\n"
            "Create a new key in Google AI Studio, enable the Generative Language API if prompted, "
            "and paste it as GOOGLE_API_KEY.\n\n"
            f"Detail: {detail}"
        )
    if (
        "not set" in low
        or "OPENAI_API_KEY is not set" in str(exc)
        or "GOOGLE_API_KEY is not set" in str(exc)
    ):
        return _config_error_reply() + f"\n\nDetail: {detail}"
    if "index required but not found" in low or (
        "unexpected response" in low and "keyword" in low and "filter" in low
    ):
        return (
            "The knowledge base (Qdrant) rejected the search because payload indexes are missing. "
            "This is common on Qdrant Cloud.\n\n"
            "Fix: from the repo root run `python -m rag.qdrant_indexes` (or re-run `rag.ingest`). "
            "Then retry your question.\n\n"
            f"Detail: {detail}"
        )
    return (
        "The AI backend could not complete this request.\n\n"
        f"Detail: {detail}"
    )


_PARTNER_ASK_CODE_EN = (
    "If you're an Iris ID partner, please send me your partner access code "
    "(e.g. the 4-digit code you received) to unlock internal product KB."
)
_PARTNER_ASK_CODE_KO = (
    "Iris ID 파트너이시면, 전달받으신 파트너 접속 코드를 입력해 주세요. "
    "그래야 내부 제품 자료(매뉴얼 등)를 바탕으로 답변드릴 수 있어요."
)
_PARTNER_GREETING_EN = (
    "Verified — welcome, Iris ID partner. How can I help? "
    "I can now answer from our internal product docs (e.g. iA1000 manual)."
)
_PARTNER_GREETING_KO = (
    "파트너 인증이 완료되었습니다. 무엇을 도와드릴까요? "
    "이제 내부 제품 매뉴얼(예: iA1000)을 바탕으로 답변드릴 수 있어요."
)


def _partner_ask_code_text(lang: str) -> str:
    return _PARTNER_ASK_CODE_KO if (lang or "").lower().startswith("ko") else _PARTNER_ASK_CODE_EN


def _partner_greeting_text(lang: str) -> str:
    return _PARTNER_GREETING_KO if (lang or "").lower().startswith("ko") else _PARTNER_GREETING_EN


def _handle_partner_gate(
    message: str,
    lang: str,
    partner_token: str | None,
) -> ChatResult | None:
    """
    Returns a ChatResult and short-circuits /chat when the turn is purely about
    partner auth (asking for code / verifying code). Returns None to continue.
    """
    if not partner_enabled():
        return None

    already = is_partner_session(partner_token)

    # A valid code anywhere in the message authenticates (or re-authenticates).
    code = extract_valid_code(message)
    if code is not None:
        token = issue_partner_token()
        return ChatResult(
            reply=_partner_greeting_text(lang),
            partner_authenticated=True,
            partner_token=token,
        )

    # Not authenticated yet AND the user is asking about partner stuff -> request code.
    if not already and message_asks_about_partner(message):
        return ChatResult(
            reply=_partner_ask_code_text(lang),
            partner_authenticated=False,
            partner_token=None,
        )

    return None


def handle_chat(
    message: str,
    region_hint: str | None = None,
    *,
    partner_token: str | None = None,
    chat_history: list[dict[str, str]] | None = None,
) -> ChatResult:
    settings = get_settings()
    lang = detect_language_iso(message)

    gate = _handle_partner_gate(message, lang, partner_token)
    if gate is not None:
        return gate

    is_partner = is_partner_session(partner_token)
    partner_meta_token = partner_token if is_partner else None
    normalized_history: list[dict[str, str]] = []
    for row in chat_history or []:
        role_raw = ""
        text_raw = ""
        if isinstance(row, dict):
            role_raw = str(row.get("role", ""))
            text_raw = str(row.get("text", ""))
        else:
            role_raw = str(getattr(row, "role", ""))
            text_raw = str(getattr(row, "text", ""))
        role = role_raw.strip().lower()
        if role in ("user", "assistant") and text_raw.strip():
            normalized_history.append({"role": role, "text": text_raw})
    if len(normalized_history) > 16:
        normalized_history = normalized_history[-16:]

    def _ok(text: str) -> ChatResult:
        return ChatResult(
            reply=text,
            partner_authenticated=is_partner,
            partner_token=partner_meta_token,
        )

    # FAQ first: no LLM, matches chatbot-rag.pdf (save tokens; works without API keys).
    faq_hit = match_faq(message, lang)
    if faq_hit:
        return _ok(faq_hit)

    if settings.llm_provider == "gemini" and settings.aslan_gemini_agent:
        if _SALES_TRIG.search(message):
            return _ok(format_handoff_sales(str(region_hint).upper() if region_hint else None))
        try:
            reply = run_gemini_agent(
                message,
                region_hint=region_hint,
                language_iso=lang,
                is_partner=is_partner,
                partner_token=partner_meta_token,
                chat_history=normalized_history,
            )
            return _ok(reply)
        except Exception as exc:
            logger.exception("gemini agent failed: %s", exc)
            return _ok(_llm_failure_reply(exc))

    try:
        routed = route_message(message)
    except Exception as exc:
        logger.warning("route_message failed, using fallback route: %s", exc)
        routed = dict(_FALLBACK_ROUTE)

    intent = str(routed.get("intent", "other"))
    needs_rag = bool(routed.get("needs_rag", False)) or looks_like_kb_or_product_query(message)
    needs_sales = bool(routed.get("needs_sales_handoff", False))
    needs_support = bool(routed.get("needs_support_handoff", False))
    product = routed.get("product_guess")
    country_raw = region_hint or routed.get("country_guess")
    country = str(country_raw).upper() if country_raw else None

    sentiment = str(routed.get("sentiment", "neutral"))
    style_hint = ""
    if sentiment == "frustrated":
        style_hint = "The user seems upset; acknowledge briefly and give clear next steps.\n"
    elif sentiment == "positive":
        style_hint = "The user sounds positive; be warm and professional.\n"

    if intent == "smalltalk":
        user = style_hint + f"Reply briefly in language ISO {lang}. User said:\n{message}"
        short_system = (
            "You are IRIS ID's friendly site assistant. Keep replies short. "
            "Do not invent product facts."
        )
        try:
            return _ok(generate(short_system, user, json_mode=False))
        except Exception as exc:
            logger.exception("smalltalk generate failed: %s", exc)
            return _ok(_llm_failure_reply(exc))

    if needs_sales:
        return _ok(format_handoff_sales(country))

    if needs_rag:
        try:
            filters = RagFilters(
                product=str(product) if product else None,
                language=lang,
                doc_type=None,
                department="support" if intent == "technical_support" else None,
            )
            best = retrieve_best_chunks(message, settings, filters)
            if not best or best[0].score < settings.rag_min_score:
                if needs_support:
                    return _ok(format_handoff_support(country))
                return _ok(
                    "I could not find a grounded answer in our technical library. "
                    + format_handoff_support(country)
                )
            chunks = [c.text for c in best]
            answer = answer_with_rag(style_hint + message, chunks)
            if needs_support:
                return _ok(answer + "\n\n" + format_handoff_support(country))
            return _ok(answer)
        except Exception as exc:
            logger.exception("RAG path failed: %s", exc)
            if needs_support:
                return _ok(format_handoff_support(country))
            return _ok(
                "Search in the knowledge base failed (is Qdrant running and ingested?). "
                + format_handoff_support(country)
                + f"\n\nDetail: {_diagnostic_from_exc(exc)}"
                + _qdrant_connection_hint(exc)
            )

    if needs_support:
        return _ok(format_handoff_support(country))

    user = style_hint + message
    try:
        return _ok(
            generate(
                "You are IRIS ID's website assistant. Be concise. If unsure, suggest support contact.",
                user,
                json_mode=False,
            )
        )
    except Exception as exc:
        logger.exception("default generate failed: %s", exc)
        return _ok(_llm_failure_reply(exc))
