"""
Partner authentication gate.

Flow in /chat:
  - No token yet, user asks partner-y questions  -> ask for access code.
  - User sends a valid code                      -> issue short-lived token (returned in response).
  - Subsequent /chat requests include that token -> is_partner_session() == True.

Tokens are kept in-process (single-server deployment). For multi-worker setups,
replace _SESSIONS with a shared store (Redis, signed JWT, etc.).
"""

from __future__ import annotations

import logging
import re
import secrets
import time

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


_SESSIONS: dict[str, tuple[float, str | None, int | None]] = {}  # token -> (expiry, code, session_id)

_PARTNER_INTENT_RE = re.compile(
    r"(?:(?<![a-z])partner(?![a-z])|"              # partner
    r"reseller|distributor|dealer|"                # reseller / distributor
    r"파트너|대리점|총판|대리|협력사|"                  # ko
    r"パートナー|代理店)",                            # ja
    re.IGNORECASE,
)

# Match a plausible code token in the user's message:
# - 3-32 chars, letters/digits/dashes (no spaces).
_POSSIBLE_CODE_RE = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9\-]{2,31})\b")

# Ingested partner_docs filenames / paths (see rag.ingest --partner).
_PARTNER_CORPUS_SOURCE_RE = re.compile(
    r"(user_manual|history_ia1000|partner_docs|installation.?plate|/assets/)",
    re.IGNORECASE,
)


def is_partner_corpus_source(metadata: dict | None) -> bool:
    """True when Qdrant chunk metadata points at aslan-rag/partner_docs content."""
    if not metadata:
        return False
    src = str(metadata.get("source", ""))
    return bool(_PARTNER_CORPUS_SOURCE_RE.search(src))


def _load_codes(settings: Settings | None = None) -> set[str]:
    s = settings or get_settings()
    raw = s.partner_codes or ""
    return {c.strip() for c in raw.split(",") if c.strip()}


def message_asks_about_partner(message: str) -> bool:
    return bool(_PARTNER_INTENT_RE.search(message or ""))



def _resolve_code_token(tok: str, env_codes: set[str]) -> str | None:
    if tok in env_codes:
        return tok
    t = tok.strip().upper()
    for c in env_codes:
        if c.upper() == t:
            return c
    try:
        from app.partner_db import is_active_code

        if is_active_code(t):
            return t
    except Exception:
        pass
    return None


def extract_valid_code(message: str, settings: Settings | None = None) -> str | None:
    """Return the first token in `message` that matches a partner access code."""
    env_codes = _load_codes(settings)
    text = (message or "").strip()
    if not text:
        return None
    resolved = _resolve_code_token(text, env_codes)
    if resolved:
        return resolved
    for m in _POSSIBLE_CODE_RE.finditer(text):
        tok = m.group(1)
        resolved = _resolve_code_token(tok, env_codes)
        if resolved:
            return resolved
    return None


def _close_session_log(session_id: int | None) -> None:
    if not session_id:
        return
    try:
        from app.partner_db import close_partner_chat_session

        close_partner_chat_session(session_id)
    except Exception:
        logger.exception("Failed to close partner chat session %s", session_id)


def _session_entry(token: str | None) -> tuple[float, str | None, int | None] | None:
    if not token:
        return None
    entry = _SESSIONS.get(token)
    if not entry:
        return None
    exp, code, session_id = entry
    if exp < time.time():
        _close_session_log(session_id)
        _SESSIONS.pop(token, None)
        return None
    return entry


def issue_partner_token(
    settings: Settings | None = None,
    *,
    code: str | None = None,
    region_hint: str | None = None,
) -> str:
    s = settings or get_settings()
    token = secrets.token_urlsafe(24)
    ttl = max(1, int(s.partner_session_ttl_minutes)) * 60
    normalized_code = (code or "").strip().upper() or None
    session_id: int | None = None
    if normalized_code:
        try:
            from app.partner_db import get_partner_id_by_code, start_partner_chat_session

            partner_id = get_partner_id_by_code(normalized_code)
            if partner_id:
                session_id = start_partner_chat_session(partner_id, region=region_hint)
        except Exception:
            logger.exception("Failed to start partner chat session for code %s", normalized_code)
    _SESSIONS[token] = (time.time() + ttl, normalized_code, session_id)
    return token


def is_partner_session(token: str | None) -> bool:
    return _session_entry(token) is not None


def partner_code_for_session(token: str | None) -> str | None:
    entry = _session_entry(token)
    if not entry:
        return None
    return entry[1]


def partner_session_id_for_token(token: str | None) -> int | None:
    entry = _session_entry(token)
    if not entry:
        return None
    return entry[2]


def record_partner_chat_activity(
    token: str | None,
    *,
    region_hint: str | None = None,
    client_timezone: str | None = None,
    client_ip: str | None = None,
    client_geo: "IpLocation | None" = None,
) -> None:
    entry = _session_entry(token)
    if not entry:
        return
    _, code, session_id = entry
    try:
        from app.geoip import IpLocation, resolve_session_geo
        from app.partner_db import (
            touch_partner_activity_by_code,
            touch_partner_chat_session,
            update_session_geo_if_empty,
        )

        if session_id:
            touch_partner_chat_session(
                session_id,
                region=region_hint,
                timezone=client_timezone,
            )
            geo: IpLocation | None = resolve_session_geo(
                client_ip=client_ip,
                client_geo=client_geo,
            )
            if geo:
                update_session_geo_if_empty(
                    session_id,
                    geo_city=geo.city,
                    geo_region=geo.region,
                    geo_country=geo.country,
                )
        if code:
            touch_partner_activity_by_code(code)
    except Exception:
        logger.exception("Failed to record partner chat activity")


def revoke_partner_token(token: str | None) -> None:
    if not token:
        return
    entry = _SESSIONS.pop(token, None)
    if entry:
        _close_session_log(entry[2])


def partner_enabled(settings: Settings | None = None) -> bool:
    if _load_codes(settings):
        return True
    try:
        from app.partner_db import count_active_partners

        return count_active_partners() > 0
    except Exception:
        return False


def query_requires_partner_auth(message: str, language_iso: str) -> bool:
    """
    True when the query would be answered from partner-only docs (aslan-rag/partner_docs).
    """
    from app.query_hints import looks_like_kb_or_product_query
    from rag.retrieve import retrieve_best_chunks, retrieve_best_chunks_from
    from rag.schemas import RagFilters

    if not partner_enabled() or not looks_like_kb_or_product_query(message):
        return False

    settings = get_settings()
    try:
        partner_hits = retrieve_best_chunks_from(
            message,
            settings,
            collection_name=settings.qdrant_collection_partner,
            base=RagFilters(language=language_iso, access="partner"),
        )
        if not partner_hits:
            return False

        top = partner_hits[0]
        if top.score < settings.rag_prefetch_min_score:
            return False

        # Partner manual / history / figures → always require code.
        if is_partner_corpus_source(top.metadata):
            logger.info(
                "Partner gate: partner corpus source=%r score=%.3f",
                top.metadata.get("source"),
                top.score,
            )
            return True

        if top.score < settings.rag_min_score:
            return False

        public_hits = retrieve_best_chunks(
            message,
            settings,
            RagFilters(language=language_iso),
            exclude_partner=True,
        )
        public_score = public_hits[0].score if public_hits else 0.0
        if public_score < settings.rag_min_score:
            return True
        if top.score > public_score + 0.03:
            return True
        return False
    except Exception as exc:
        logger.warning("Partner KB probe failed: %s", exc)
        return False
