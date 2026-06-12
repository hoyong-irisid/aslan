"""Client IP extraction and geolocation lookup for partner activity logs."""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass

import httpx
from fastapi import Request

from config.settings import get_settings

logger = logging.getLogger(__name__)

_GEO_CACHE: dict[str, IpLocation] = {}
_MAX_GEO_FIELD_LEN = 120


@dataclass(frozen=True)
class IpLocation:
    city: str | None = None
    region: str | None = None
    country: str | None = None

    def label(self) -> str:
        parts: list[str] = []
        if self.city:
            parts.append(self.city)
        if self.region:
            parts.append(self.region)
        if self.country:
            parts.append(self.country)
        return ", ".join(parts)


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        return True


def _clean_geo_field(value: str | None) -> str | None:
    v = (value or "").strip()
    if not v:
        return None
    return v[:_MAX_GEO_FIELD_LEN]


def client_geo_from_fields(
    city: str | None,
    region: str | None,
    country: str | None,
) -> IpLocation | None:
    loc = IpLocation(
        city=_clean_geo_field(city),
        region=_clean_geo_field(region),
        country=_clean_geo_field(country),
    )
    if not loc.city and not loc.region and not loc.country:
        return None
    return loc


def extract_client_ip(request: Request) -> str | None:
    """Best-effort client IP from common reverse-proxy headers."""
    for name in ("cf-connecting-ip", "true-client-ip", "x-real-ip"):
        raw = (request.headers.get(name) or "").strip()
        if raw and not _is_private_ip(raw):
            return raw

    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        for part in forwarded.split(","):
            candidate = part.strip()
            if candidate and not _is_private_ip(candidate):
                return candidate

    if request.client and request.client.host:
        host = request.client.host.strip()
        if host and not _is_private_ip(host):
            return host
    return None


def _lookup_ip_api(ip: str) -> IpLocation | None:
    url = f"http://ip-api.com/json/{ip}"
    params = {"fields": "status,message,country,regionName,region,city"}
    settings = get_settings()
    with httpx.Client(timeout=settings.geoip_timeout_sec) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    if data.get("status") != "success":
        return None
    city = _clean_geo_field(data.get("city"))
    region_code = _clean_geo_field(data.get("region"))
    region_name = _clean_geo_field(data.get("regionName"))
    country = _clean_geo_field(data.get("country"))
    region = region_code or region_name
    if not city and not region and not country:
        return None
    return IpLocation(city=city, region=region, country=country)


def _lookup_ipwhois(ip: str) -> IpLocation | None:
    settings = get_settings()
    with httpx.Client(timeout=settings.geoip_timeout_sec) as client:
        resp = client.get(f"https://ipwho.is/{ip}")
        resp.raise_for_status()
        data = resp.json()
    if not data.get("success"):
        return None
    city = _clean_geo_field(data.get("city"))
    region = _clean_geo_field(data.get("region"))
    country = _clean_geo_field(data.get("country"))
    if not city and not region and not country:
        return None
    return IpLocation(city=city, region=region, country=country)


def lookup_ip_location(ip: str) -> IpLocation | None:
    settings = get_settings()
    if not settings.geoip_enabled:
        return None
    candidate = (ip or "").strip()
    if not candidate or _is_private_ip(candidate):
        return None
    if candidate in _GEO_CACHE:
        return _GEO_CACHE[candidate]
    loc: IpLocation | None = None
    try:
        loc = _lookup_ip_api(candidate)
    except Exception:
        logger.debug("ip-api lookup failed for %s", candidate, exc_info=True)
    if loc is None:
        try:
            loc = _lookup_ipwhois(candidate)
        except Exception:
            logger.debug("ipwho.is lookup failed for %s", candidate, exc_info=True)
    if loc:
        _GEO_CACHE[candidate] = loc
    return loc


def resolve_session_geo(
    *,
    client_ip: str | None = None,
    client_geo: IpLocation | None = None,
) -> IpLocation | None:
    """Prefer server-side IP lookup; fall back to browser-provided geo."""
    if client_ip:
        geo = lookup_ip_location(client_ip.strip())
        if geo:
            return geo
    return client_geo
