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


_SESSIONS: dict[str, float] = {}  # token -> unix expiry

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


def issue_partner_token(settings: Settings | None = None) -> str:
    s = settings or get_settings()
    token = secrets.token_urlsafe(24)
    ttl = max(1, int(s.partner_session_ttl_minutes)) * 60
    _SESSIONS[token] = time.time() + ttl
    return token


def is_partner_session(token: str | None) -> bool:
    if not token:
        return False
    exp = _SESSIONS.get(token)
    if not exp:
        return False
    if exp < time.time():
        _SESSIONS.pop(token, None)
        return False
    return True


def revoke_partner_token(token: str | None) -> None:
    if token:
        _SESSIONS.pop(token, None)


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
