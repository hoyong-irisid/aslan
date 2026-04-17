from langdetect import detect

from app.contacts import resolve_region
from app.faq import match_faq
from app.llm import answer_with_rag, generate, route_message
from config.settings import get_settings
from rag.embeddings import embed_texts
from rag.query import search_chunks
from rag.rerank import rerank
from rag.schemas import RagFilters


def detect_language_iso(text: str) -> str:
    try:
        return detect(text)[:2]
    except Exception:
        return "en"


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


def handle_chat(message: str, region_hint: str | None = None) -> str:
    settings = get_settings()
    lang = detect_language_iso(message)

    routed = route_message(message)
    intent = str(routed.get("intent", "other"))
    needs_rag = bool(routed.get("needs_rag", False))
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
        return generate(short_system, user, json_mode=False)

    if needs_sales:
        return format_handoff_sales(country)

    faq_hit = match_faq(message, lang)
    if faq_hit:
        return faq_hit

    if needs_rag:
        filters = RagFilters(
            product=str(product) if product else None,
            language=lang,
            doc_type=None,
            department="support" if intent == "technical_support" else None,
        )
        qvec = embed_texts([message])[0]
        found = search_chunks(
            question_vector=qvec,
            filters=filters,
            top_k=settings.rag_search_top_k,
        )
        best = rerank(message, found, settings.rag_final_top_k)
        if not best or best[0].score < settings.rag_min_score:
            if needs_support:
                return format_handoff_support(country)
            return (
                "I could not find a grounded answer in our technical library. "
                + format_handoff_support(country)
            )
        chunks = [c.text for c in best]
        answer = answer_with_rag(style_hint + message, chunks)
        if needs_support:
            return answer + "\n\n" + format_handoff_support(country)
        return answer

    if needs_support:
        return format_handoff_support(country)

    user = style_hint + message
    return generate(
        "You are IRIS ID's website assistant. Be concise. If unsure, suggest support contact.",
        user,
        json_mode=False,
    )
