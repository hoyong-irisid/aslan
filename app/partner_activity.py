"""Partner inactivity threshold (auto-deactivate after N days without activity)."""

from __future__ import annotations

from app.partner_db import get_partner_setting, set_partner_setting

_SETTING_KEY = "partner_inactivity_days"
_DEFAULT_DAYS = 90
_MIN_DAYS = 1
_MAX_DAYS = 3650


def get_inactivity_days() -> int:
    raw = get_partner_setting(_SETTING_KEY, str(_DEFAULT_DAYS)).strip()
    try:
        days = int(raw)
    except ValueError:
        days = _DEFAULT_DAYS
    return max(_MIN_DAYS, min(_MAX_DAYS, days))


def save_inactivity_days(days: int) -> int:
    normalized = max(_MIN_DAYS, min(_MAX_DAYS, int(days)))
    set_partner_setting(_SETTING_KEY, str(normalized))
    return normalized
