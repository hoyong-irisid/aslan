"""Client IP extraction and geolocation lookup for partner activity logs."""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from functools import lru_cache

import httpx
from fastapi import Request

from config.settings import get_settings

logger = logging.getLogger(__name__)


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
        return ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return True


def extract_client_ip(request: Request) -> str | None:
    """Best-effort client IP; prefers the leftmost public IP in X-Forwarded-For."""
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        for part in forwarded.split(","):
            candidate = part.strip()
            if candidate and not _is_private_ip(candidate):
                return candidate
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip and not _is_private_ip(real_ip):
        return real_ip
    if request.client and request.client.host:
        host = request.client.host.strip()
        if host and not _is_private_ip(host):
            return host
        return host or None
    return None


@lru_cache(maxsize=2048)
def lookup_ip_location(ip: str) -> IpLocation | None:
    settings = get_settings()
    if not settings.geoip_enabled:
        return None
    if not ip or _is_private_ip(ip):
        return None
    url = f"http://ip-api.com/json/{ip}"
    params = {"fields": "status,message,country,regionName,region,city"}
    try:
        with httpx.Client(timeout=settings.geoip_timeout_sec) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.debug("GeoIP lookup failed for %s", ip, exc_info=True)
        return None
    if data.get("status") != "success":
        return None
    city = (data.get("city") or "").strip() or None
    region_code = (data.get("region") or "").strip() or None
    region_name = (data.get("regionName") or "").strip() or None
    country = (data.get("country") or "").strip() or None
    region = region_code or region_name
    if not city and not region and not country:
        return None
    return IpLocation(city=city, region=region, country=country)
