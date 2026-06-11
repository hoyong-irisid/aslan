"""Partner signup, OTP verification, and admin API routes."""

from __future__ import annotations

import json
import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.contacts import load_contacts
from app.partner_db import (
    admin_stats,
    consume_otp,
    create_otp_challenge,
    deactivate_partner,
    get_partner,
    get_partner_by_email,
    insert_partner,
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
    region_key: str = Field(min_length=1, max_length=80)
    country_iso: str | None = Field(default=None, max_length=2)


class SignupVerifyRequest(BaseModel):
    email: str
    otp: str = Field(min_length=4, max_length=8)


class AdminPartnerCreate(BaseModel):
    email: str
    name: str = Field(min_length=1, max_length=120)
    company: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    region_key: str = Field(min_length=1, max_length=80)
    country_iso: str | None = Field(default=None, max_length=2)
    send_email: bool = True


class AdminPartnerUpdate(BaseModel):
    email: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    company: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    region_key: str | None = Field(default=None, min_length=1, max_length=80)
    country_iso: str | None = Field(default=None, max_length=2)
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
    return {
        "id": p["id"],
        "email": p["email"],
        "name": p["name"],
        "company": p["company"],
        "phone": p["phone"],
        "region_key": p["region_key"],
        "country_iso": p["country_iso"],
        "active": p["active"],
        "source": p["source"],
        "verified_at": p["verified_at"],
        "created_at": p["created_at"],
        "updated_at": p["updated_at"],
    }


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
                {"key": "global", "label": "Global / Other", "countries": []},
            ],
            "default_region": "global",
        }


@router.post("/signup/start")
def signup_start(body: SignupStartRequest) -> dict[str, str]:
    settings = get_settings()
    _validate_region(body.region_key)
    try:
        email = normalize_email(body.email)
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
            "region_key": body.region_key.strip(),
            "country_iso": (body.country_iso or "").strip().upper() or None,
        }
    )
    ttl = max(5, int(settings.partner_otp_ttl_minutes or 10))
    create_otp_challenge(email, otp, payload, ttl)
    try:
        send_partner_otp_email(to_email=email, otp=otp, settings=settings)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to send OTP email: {e}") from e
    return {"status": "otp_sent", "message": "Verification code sent to your email"}


@router.post("/signup/verify")
def signup_verify(body: SignupVerifyRequest) -> dict[str, Any]:
    settings = get_settings()
    try:
        email = normalize_email(body.email)
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
    _validate_region(body.region_key)
    try:
        partner = insert_partner(
            email=body.email,
            name=body.name,
            company=body.company,
            phone=body.phone,
            region_key=body.region_key,
            country_iso=body.country_iso,
            source="admin",
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

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


@router.patch("/admin/partners/{partner_id}")
def admin_update_partner(
    partner_id: int,
    body: AdminPartnerUpdate,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    if get_partner(partner_id) is None:
        raise HTTPException(status_code=404, detail="Partner not found")
    fields = body.model_dump(exclude_unset=True)
    if "region_key" in fields and fields["region_key"]:
        _validate_region(fields["region_key"])
    try:
        partner = update_partner(partner_id, fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
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


@router.delete("/admin/partners/{partner_id}")
def admin_delete_partner(
    partner_id: int,
    x_partner_admin_key: str | None = Header(default=None, alias="X-Partner-Admin-Key"),
) -> dict[str, str]:
    settings = get_settings()
    _require_admin(x_partner_admin_key, settings)
    if get_partner(partner_id) is None:
        raise HTTPException(status_code=404, detail="Partner not found")
    deactivate_partner(partner_id)
    return {"status": "deactivated"}
