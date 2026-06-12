"""Partner signup email domain allow/block lists."""

from __future__ import annotations

import json
import re

from app.partner_db import get_partner_setting, set_partner_setting

_DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$"
)
_SETTING_ALLOWED = "signup_allowed_domains"
_SETTING_BLOCKED = "signup_blocked_domains"


def normalize_domain_token(raw: str) -> str | None:
    """Accept @example.com or example.com; return example.com."""
    s = (raw or "").strip().lower()
    if not s:
        return None
    if s.startswith("@"):
        s = s[1:]
    if not _DOMAIN_RE.match(s):
        return None
    return s


def parse_domain_list(text: str) -> list[str]:
    """Parse newline/comma-separated domain tokens; return @example.com display form."""
    if not text:
        return []
    parts = re.split(r"[\n,]+", text)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        norm = normalize_domain_token(part)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(f"@{norm}")
    return out


def domains_to_text(domains: list[str]) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for item in domains:
        norm = normalize_domain_token(item)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        lines.append(f"@{norm}")
    return "\n".join(lines)


def _load_domain_setting(key: str) -> list[str]:
    """Load stored domain list; tolerate JSON arrays, JSON strings, or plain text."""
    raw = get_partner_setting(key, "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return parse_domain_list(raw)
    if isinstance(data, list):
        return parse_domain_list("\n".join(str(x) for x in data))
    if isinstance(data, str):
        return parse_domain_list(data)
    return []


def get_signup_domain_rules() -> dict[str, list[str]]:
    return {
        "allowed_domains": _load_domain_setting(_SETTING_ALLOWED),
        "blocked_domains": _load_domain_setting(_SETTING_BLOCKED),
    }


def save_signup_domain_rules_from_text(
    *,
    allowed_domains_text: str = "",
    blocked_domains_text: str = "",
) -> dict[str, list[str]]:
    allowed = parse_domain_list(allowed_domains_text)
    blocked = parse_domain_list(blocked_domains_text)
    set_partner_setting(_SETTING_ALLOWED, json.dumps(allowed))
    set_partner_setting(_SETTING_BLOCKED, json.dumps(blocked))
    return {"allowed_domains": allowed, "blocked_domains": blocked}


def email_domain(email: str) -> str:
    parts = (email or "").strip().lower().split("@", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("Invalid email address")
    return parts[1]


def validate_signup_email_domain(email: str) -> None:
    """Raise ValueError when email domain is blocked or not on the allow list."""
    domain = email_domain(email)
    rules = get_signup_domain_rules()
    blocked = {normalize_domain_token(d) for d in rules["blocked_domains"]}
    allowed = {normalize_domain_token(d) for d in rules["allowed_domains"]}

    if domain in blocked:
        raise ValueError("This email domain is not allowed for partner registration.")

    if not allowed:
        raise ValueError(
            "Partner registration is limited to approved email domains. "
            "Contact your Iris ID representative if you need access."
        )

    if domain not in allowed:
        allowed_display = ", ".join(sorted(rules["allowed_domains"]))
        raise ValueError(
            f"Registration is limited to approved partner email domains ({allowed_display})."
        )
