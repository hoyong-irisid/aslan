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


def get_signup_domain_rules() -> dict[str, list[str]]:
    allowed = json.loads(get_partner_setting(_SETTING_ALLOWED, "[]"))
    blocked = json.loads(get_partner_setting(_SETTING_BLOCKED, "[]"))
    if not isinstance(allowed, list):
        allowed = []
    if not isinstance(blocked, list):
        blocked = []
    return {
        "allowed_domains": parse_domain_list("\n".join(str(x) for x in allowed)),
        "blocked_domains": parse_domain_list("\n".join(str(x) for x in blocked)),
    }


def save_signup_domain_rules(*, allowed_domains: list[str], blocked_domains: list[str]) -> dict[str, list[str]]:
    allowed = parse_domain_list("\n".join(allowed_domains))
    blocked = parse_domain_list("\n".join(blocked_domains))
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
