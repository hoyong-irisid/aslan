import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel


class FaqEntry(BaseModel):
    triggers: list[str]
    answer: str


class FaqFile(BaseModel):
    entries: list[FaqEntry]


@lru_cache
def load_faq(language_iso: str) -> FaqFile:
    root = Path(__file__).resolve().parents[1]
    path = root / "faq" / f"faq_{language_iso}.json"
    if not path.exists():
        path = root / "faq" / "faq_en.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return FaqFile.model_validate(data)


def match_faq(message: str, language_iso: str) -> str | None:
    text = message.lower()
    faq = load_faq(language_iso)
    for entry in faq.entries:
        if any(t.lower() in text for t in entry.triggers):
            return entry.answer
    return None
