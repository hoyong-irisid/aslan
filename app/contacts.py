import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel


class Person(BaseModel):
    name: str
    email: str
    phone: str


class Region(BaseModel):
    label: str
    countries: list[str]
    sales: Person
    support: Person


class ContactsFile(BaseModel):
    regions: dict[str, Region]
    default_region: str


@lru_cache
def load_contacts() -> ContactsFile:
    root = Path(__file__).resolve().parents[1]
    for name in ("config/contacts.json", "config/contacts.example.json"):
        p = root / name
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            parsed = ContactsFile.model_validate(data)
            if parsed.default_region not in parsed.regions:
                raise ValueError("contacts default_region must match a key in regions")
            return parsed
    raise RuntimeError("No contacts file found. Add config/contacts.json (see contacts.example.json).")


def resolve_region(country_iso2: str | None) -> Region:
    c = load_contacts()
    if country_iso2:
        cc = country_iso2.upper()
        for _key, region in c.regions.items():
            if cc in region.countries:
                return region
    return c.regions[c.default_region]
