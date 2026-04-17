import json
from typing import Any

import httpx

from config.settings import get_settings
from rag.prompts import RAG_SYSTEM, ROUTER_SYSTEM


def _gemini_generate(model: str, system: str, user: str, json_mode: bool) -> str:
    settings = get_settings()
    key = settings.google_api_key
    if not key:
        raise RuntimeError("GOOGLE_API_KEY is not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
    }
    if json_mode:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    with httpx.Client(timeout=120) as client:
        r = client.post(url, params={"key": key}, json=body)
        r.raise_for_status()
        data = r.json()
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


def _openai_chat(system: str, user: str, json_mode: bool) -> str:
    settings = get_settings()
    key = settings.openai_api_key
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    url = "https://api.openai.com/v1/chat/completions"
    body: dict[str, Any] = {
        "model": settings.openai_chat_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    with httpx.Client(timeout=120) as client:
        r = client.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {key}"},
        )
        r.raise_for_status()
        data = r.json()
    return data["choices"][0]["message"]["content"]


def generate(system: str, user: str, *, json_mode: bool = False) -> str:
    settings = get_settings()
    if settings.llm_provider == "gemini":
        return _gemini_generate(settings.gemini_chat_model, system, user, json_mode)
    return _openai_chat(system, user, json_mode)


def route_message(message: str) -> dict[str, Any]:
    raw = generate(ROUTER_SYSTEM, message, json_mode=True)
    return json.loads(raw)


def answer_with_rag(message: str, chunks: list[str]) -> str:
    context = "\n\n---\n\n".join(chunks)
    user = f"Context:\n{context}\n\nUser question:\n{message}"
    return generate(RAG_SYSTEM, user, json_mode=False)
