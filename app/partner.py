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


def _load_codes(settings: Settings | None = None) -> set[str]:
    s = settings or get_settings()
    raw = s.partner_codes or ""
    return {c.strip() for c in raw.split(",") if c.strip()}


def message_asks_about_partner(message: str) -> bool:
    return bool(_PARTNER_INTENT_RE.search(message or ""))


def extract_valid_code(message: str, settings: Settings | None = None) -> str | None:
    """Return the first token in `message` that matches a configured partner code."""
    codes = _load_codes(settings)
    if not codes:
        return None
    text = (message or "").strip()
    if not text:
        return None
    # Exact-match shortcut: user types just the code.
    if text in codes:
        return text
    for m in _POSSIBLE_CODE_RE.finditer(text):
        tok = m.group(1)
        if tok in codes:
            return tok
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
    return bool(_load_codes(settings))
