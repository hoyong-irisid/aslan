"""Partner signup, OTP verification, and admin API routes."""

from __future__ import annotations

import csv
import io
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.contacts import load_contacts
from app.countries import (
    country_name_for_iso,
    list_countries_for_select,
    region_key_for_country_iso,
    resolve_country_name,
)
from app.partner_activity import get_inactivity_days, save_inactivity_days
from app.partner_domains import (
    domains_to_text,
    get_signup_domain_rules,
    save_signup_domain_rules_from_text,
    validate_signup_email_domain,
)
from app.partner_db import (
    activate_partner,
    admin_dashboard_data,
    admin_stats,
    consume_otp,
    create_otp_challenge,
    deactivate_inactive_partners,
    deactivate_partner,
    delete_partner,
    get_partner,
    get_partner_by_email,
    insert_partner,
    list_partner_chat_sessions,
    list_partners,
    normalize_email,
    regenerate_code,
    update_partner,
)
from app.partner_mail import send_partner_code_email, send_partner_otp_email
from config.settings import Settings, get_settings

router = APIRouter(prefix="/api/partner", tags=["partner-registry"])


class SignupStartRequest(BaseModel):
    email: str
    name: str = Field(min_length=1, max_length=120)
    company: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    country: str = Field(min_length=1, max_length=120)


class SignupVerifyRequest(BaseModel):
    email: str
    otp: str = Field(min_length=4, max_length=8)


class AdminPartnerCreate(BaseModel):
    email: str
    name: str = Field(min_length=1, max_length=120)
    company: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    country: str = Field(min_length=1, max_length=120)
    send_email: bool = True


class AdminDashboardRequest(BaseModel):
    include_inactive: bool = True


class AdminDomainSettingsUpdate(BaseModel):
    allowed_domains_text: str = ""
    blocked_domains_text: str = ""


class AdminActivityTimingUpdate(BaseModel):
    inactivity_days: int = Field(default=90, ge=1, le=3650)


class AdminPartnerUpdate(BaseModel):
    email: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    company: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    country: str | None = Field(default=None, min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=2000)
    active: bool | None = None


def _require_admin(x_partner_admin_key: str | None, settings: Settings) -> None:
    expected = (settings.partner_admin_api_key or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Partner admin is not configured (PARTNER_ADMIN_API_KEY)",
        )
    if not x_partner_admin_key or not secrets.compare_digest(
        x_partner_admin_key.strip(), expected
    ):
        raise HTTPException(status_code=401, detail="Invalid admin key")


def _validate_region(region_key: str) -> None:
    try:
        contacts = load_contacts()
    except RuntimeError:
        return
    if region_key not in contacts.regions:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown region_key. Valid: {', '.join(contacts.regions.keys())}",
        )


def _public_partner(p: dict[str, Any]) -> dict[str, Any]:
    country = p.get("country") or country_name_for_iso(p.get("country_iso"))
    return {
        "id": p["id"],
        "email": p["email"],
        "name": p["name"],
        "company": p["company"],
        "phone": p["phone"],
        "region_key": p["region_key"],
        "country_iso": p["country_iso"],
        "country": country or p.get("country_iso"),
        "note": p.get("note"),
        "active": p["active"],
        "source": p["source"],
        "verified_at": p["verified_at"],
        "created_at": p["created_at"],
        "updated_at": p["updated_at"],
    }


@router.get("/countries")
def list_countries() -> dict[str, Any]:
    countries = list_countries_for_select()
    return {"countries": countries, "default_country": "United States"}


@router.get("/regions")
def list_regions() -> dict[str, Any]:
    try:
        contacts = load_contacts()
        regions = [
            {"key": k, "label": v.label, "countries": v.countries}
            for k, v in contacts.regions.items()
        ]
        return {"regions": regions, "default_region": contacts.default_region}
    except RuntimeError:
        return {
            "regions": [
                {"key": "north_america", "label": "North America", "countries": []},
                {"key": "south_america", "label": "South America", "countries": []},
                {"key": "east_asia", "label": "East Asia", "countries": []},
                {"key": "middle_east", "label": "Middle East", "countries": []},
                {"key": "europe", "label": "Europe", "countries": []},
                {"key": "africa", "label": "Africa", "countries": []},
            ],
            "default_region": "north_america",
        }


def _resolve_signup_country(country: str) -> tuple[str, str, str]:
    try:
        iso, country_name = resolve_country_name(country)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    region_key = region_key_for_country_iso(iso)
    _validate_region(region_key)
    return iso, country_name, region_key


@router.get("/signup/domain-rules")
def signup_domain_rules() -> dict[str, Any]:
    rules = get_signup_domain_rules()
    allowed = rules["allowed_domains"]
    hint = ""
    if allowed:
        hint = f"Registration is limited to approved partner email domains ({', '.join(allowed)})."
    return {
        "allowed_domains": allowed,
        "hint": hint,
    }


@router.post("/signup/start")
def signup_start(body: SignupStartRequest) -> dict[str, Any]:
    settings = get_settings()
    iso, country_name, region_key = _resolve_signup_country(body.country)
    try:
        email = normalize_email(body.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        validate_signup_email_domain(email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    existing = get_partner_by_email(email)
    if existing and existing["active"]:
        raise HTTPException(
            status_code=409,
            detail="This email is already registered as an active partner",
        )

    otp = f"{secrets.randbelow(1_000_000):06d}"
    payload = json.dumps(
        {
            "email": email,
            "name": body.name.strip(),
            "company": body.company.strip(),
            "phone": (body.phone or "").strip() or None,
            "region_key": region_key,
            "country_iso": iso,
            "country": country_name,
        }
    )
    ttl = max(5, int(settings.partner_otp_ttl_minutes or 10))
    create_otp_challenge(email, otp, payload, ttl)
    try:
        send_partner_otp_email(to_email=email, otp=otp, settings=settings)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to send OTP email: {e}") from e
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl)
    return {
        "status": "otp_sent",
        "message": "Verification code sent to your email",
        "expires_at": expires_at.replace(microsecond=0).isoformat(),
        "ttl_minutes": ttl,
    }


@router.post("/signup/verify")
def signup_verify(body: SignupVerifyRequest) -> dict[str, Any]:
    settings = get_settings()
    try:
        email = normalize_email(body.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        validate_signup_email_domain(email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    payload_json = consume_otp(email, body.otp.strip())
    if not payload_json:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    data = json.loads(payload_json)
    _validate_region(data["region_key"])

    try:
        partner = insert_partner(
            email=data["email"],
            name=data["name"],
            company=data["company"],
            phone=data.get("phone"),
            region_key=data["region_key"],
            country_iso=data.get("country_iso"),
            country=data.get("country"),
            source="signup",
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    try:
        send_partner_code_email(
            to_email=partner["email"],
            name=partner["name"],
            code=partner["code"],
            settings=settings,
        )
    except Exception as e:
        return {
            "status": "verified",
            "partner": _public_partner(partner),
            "code": partner["code"],
            "email_warning": f"Partner created but code email failed: {e}",
        }

    return {
        "status": "verified",
        "partner": _public_partner(partner),
        "message": "Partner access code sent to your email",
    }


@router.post("/admin/dashboard")
def admin_dashboard(
    body: AdminDashboardRequest | None = None,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    """Live stats + partner list (POST avoids reverse-proxy GET caching)."""
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    req = body or AdminDashboardRequest()
    deactivate_inactive_partners(get_inactivity_days())
    return admin_dashboard_data(include_inactive=req.include_inactive)


@router.get("/admin/stats")
def admin_get_stats(
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    return admin_stats()


@router.get("/admin/partners")
def admin_list_partners(
    include_inactive: bool = False,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    partners = list_partners(include_inactive=include_inactive)
    return {"partners": partners}


@router.post("/admin/partners")
def admin_create_partner(
    body: AdminPartnerCreate,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    iso, country_name, region_key = _resolve_signup_country(body.country)
    try:
        email = normalize_email(body.email)
        partner = insert_partner(
            email=email,
            name=body.name,
            company=body.company,
            phone=body.phone,
            region_key=region_key,
            country_iso=iso,
            country=country_name,
            source="admin",
        )
    except ValueError as e:
        existing = get_partner_by_email(body.email)
        if existing:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": str(e),
                    "partner": existing,
                },
            ) from e
        raise HTTPException(status_code=409, detail=str(e)) from e

    logger.info(
        "Admin created partner id=%s email=%s code=%s",
        partner["id"],
        partner["email"],
        partner["code"],
    )

    email_warning = None
    if body.send_email:
        try:
            send_partner_code_email(
                to_email=partner["email"],
                name=partner["name"],
                code=partner["code"],
                settings=settings,
            )
        except Exception as e:
            email_warning = str(e)

    out: dict[str, Any] = {"partner": partner}
    if email_warning:
        out["email_warning"] = email_warning
    return out


def _admin_update_partner_impl(
    partner_id: int,
    body: AdminPartnerUpdate,
    settings: Settings,
) -> dict[str, Any]:
    if get_partner(partner_id) is None:
        raise HTTPException(status_code=404, detail="Partner not found")
    fields = body.model_dump(exclude_unset=True)
    if "country" in fields and fields["country"]:
        iso, country_name, region_key = _resolve_signup_country(fields["country"])
        fields["country_iso"] = iso
        fields["country"] = country_name
        fields["region_key"] = region_key
    if "region_key" in fields and fields["region_key"]:
        _validate_region(fields["region_key"])
    try:
        partner = update_partner(partner_id, fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    logger.info(
        "Admin updated partner id=%s email=%s active=%s",
        partner["id"],
        partner["email"],
        partner["active"],
    )
    return {"partner": partner}


@router.patch("/admin/partners/{partner_id}")
def admin_update_partner(
    partner_id: int,
    body: AdminPartnerUpdate,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    return _admin_update_partner_impl(partner_id, body, settings)


@router.post("/admin/partners/{partner_id}/update")
def admin_update_partner_post(
    partner_id: int,
    body: AdminPartnerUpdate,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    """POST alias for update (some Apache/cPanel proxies block PATCH)."""
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    return _admin_update_partner_impl(partner_id, body, settings)


@router.post("/admin/partners/{partner_id}/activate")
def admin_activate_partner(
    partner_id: int,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    try:
        partner = activate_partner(partner_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"partner": partner}


@router.post("/admin/partners/{partner_id}/deactivate")
def admin_deactivate_partner_post(
    partner_id: int,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    try:
        partner = deactivate_partner(partner_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"partner": partner}


@router.post("/admin/partners/{partner_id}/regenerate-code")
def admin_regenerate_code(
    partner_id: int,
    send_email: bool = True,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    try:
        partner = regenerate_code(partner_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if send_email:
        try:
            send_partner_code_email(
                to_email=partner["email"],
                name=partner["name"],
                code=partner["code"],
                settings=settings,
            )
        except Exception as e:
            return {"partner": partner, "email_warning": str(e)}
    return {"partner": partner}


@router.post("/admin/partners/{partner_id}/delete")
def admin_delete_partner_post(
    partner_id: int,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    """Permanently remove a partner record (Apache-safe POST)."""
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    try:
        partner = delete_partner(partner_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"status": "deleted", "partner": partner}


@router.delete("/admin/partners/{partner_id}")
def admin_delete_partner(
    partner_id: int,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    try:
        partner = delete_partner(partner_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"status": "deleted", "partner": partner}


@router.get("/admin/settings/domains")
def admin_get_domain_settings(
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    rules = get_signup_domain_rules()
    return {
        "allowed_domains": rules["allowed_domains"],
        "blocked_domains": rules["blocked_domains"],
        "allowed_domains_text": domains_to_text(rules["allowed_domains"]),
        "blocked_domains_text": domains_to_text(rules["blocked_domains"]),
    }


@router.post("/admin/settings/domains")
def admin_save_domain_settings(
    body: AdminDomainSettingsUpdate,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    saved = save_signup_domain_rules_from_text(
        allowed_domains_text=body.allowed_domains_text,
        blocked_domains_text=body.blocked_domains_text,
    )
    return {
        "status": "saved",
        "allowed_domains": saved["allowed_domains"],
        "blocked_domains": saved["blocked_domains"],
        "allowed_domains_text": domains_to_text(saved["allowed_domains"]),
        "blocked_domains_text": domains_to_text(saved["blocked_domains"]),
    }


@router.get("/admin/settings/activity-timing")
def admin_get_activity_timing(
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    days = get_inactivity_days()
    return {"inactivity_days": days, "unit": "day(s)"}


@router.post("/admin/settings/activity-timing")
def admin_save_activity_timing(
    body: AdminActivityTimingUpdate,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    days = save_inactivity_days(body.inactivity_days)
    return {"status": "saved", "inactivity_days": days, "unit": "day(s)"}


_ET = ZoneInfo("America/New_York")

_TZ_LOCATION_LABELS = {
    "America/New_York": "United States (Eastern)",
    "America/Chicago": "United States (Central)",
    "America/Denver": "United States (Mountain)",
    "America/Los_Angeles": "United States (Pacific)",
    "America/Phoenix": "United States (Arizona)",
    "America/Anchorage": "United States (Alaska)",
    "Pacific/Honolulu": "United States (Hawaii)",
    "Asia/Seoul": "South Korea",
    "Asia/Tokyo": "Japan",
    "Europe/London": "United Kingdom",
    "Europe/Paris": "France",
    "Europe/Berlin": "Germany",
    "Asia/Dubai": "United Arab Emirates",
    "Asia/Singapore": "Singapore",
    "Australia/Sydney": "Australia (Sydney)",
}


def _parse_iso_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s" if sec else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def _session_duration_seconds(row: dict[str, Any]) -> int:
    stored = row.get("duration_seconds")
    if stored is not None and row.get("ended_at"):
        return max(0, int(stored))
    started = _parse_iso_dt(row.get("started_at"))
    ended = _parse_iso_dt(row.get("last_activity_at") or row.get("ended_at"))
    if not started or not ended:
        return 0
    return max(0, int((ended - started).total_seconds()))


def _timezone_location_label(timezone_name: str) -> str:
    tz = (timezone_name or "").strip()
    if not tz:
        return ""
    if tz in _TZ_LOCATION_LABELS:
        return _TZ_LOCATION_LABELS[tz]
    if "/" in tz:
        return tz.split("/", 1)[1].replace("_", " ")
    return tz


def _format_geo_location(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("geo_city", "geo_region", "geo_country"):
        val = (row.get(key) or "").strip()
        if val:
            parts.append(val)
    return ", ".join(parts)


def _session_location(row: dict[str, Any], partner: dict[str, Any] | None = None) -> str:
    geo = _format_geo_location(row)
    if geo:
        return geo
    tz = (row.get("timezone") or "").strip()
    if tz:
        return _timezone_location_label(tz)
    region = (row.get("region") or "").strip().upper()
    if region:
        name = country_name_for_iso(region)
        if name:
            return f"{name} ({region})"
        return region
    if partner:
        country = partner.get("country") or country_name_for_iso(partner.get("country_iso"))
        if country:
            return str(country)
    return "—"


def _format_log_datetime(started_iso: str | None) -> tuple[str, str]:
    started = _parse_iso_dt(started_iso)
    if not started:
        return "—", "—"
    dt_et = started.astimezone(_ET)
    return dt_et.strftime("%Y-%m-%d"), dt_et.strftime("%I:%M:%S %p %Z")


def _serialize_activity_log(
    row: dict[str, Any],
    partner: dict[str, Any] | None = None,
) -> dict[str, Any]:
    date_str, time_str = _format_log_datetime(row.get("started_at"))
    duration_seconds = _session_duration_seconds(row)
    return {
        "id": row["id"],
        "started_at": row.get("started_at"),
        "date": date_str,
        "time": time_str,
        "region": row.get("region"),
        "timezone": row.get("timezone"),
        "geo_city": row.get("geo_city"),
        "geo_region": row.get("geo_region"),
        "geo_country": row.get("geo_country"),
        "location": _session_location(row, partner),
        "duration_seconds": duration_seconds,
        "duration": _format_duration(duration_seconds),
        "ended_at": row.get("ended_at"),
    }


def _activity_logs_csv(partner: dict[str, Any], logs: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Partner", partner.get("name", ""), partner.get("email", "")])
    writer.writerow(["Company", partner.get("company", ""), ""])
    writer.writerow([])
    writer.writerow(["Date", "Time (ET)", "Location", "Duration"])
    for row in logs:
        item = _serialize_activity_log(row, partner)
        writer.writerow([item["date"], item["time"], item["location"], item["duration"]])
    return buf.getvalue()


@router.get("/admin/partners/{partner_id}/activity-logs")
def admin_partner_activity_logs(
    partner_id: int,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    partner = get_partner(partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    rows = list_partner_chat_sessions(partner_id)
    public = _public_partner(partner)
    return {
        "partner": public,
        "logs": [_serialize_activity_log(r, public) for r in rows],
    }


@router.get("/admin/partners/{partner_id}/activity-logs/export")
def admin_export_partner_activity_logs(
    partner_id: int,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> Response:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    partner = get_partner(partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    public = _public_partner(partner)
    rows = list_partner_chat_sessions(partner_id)
    csv_text = _activity_logs_csv(public, rows)
    slug = (public.get("company") or "partner").replace(" ", "-").lower()[:40]
    filename = f"partner-{partner_id}-{slug}-activity.csv"
    return Response(
        content="\ufeff" + csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
