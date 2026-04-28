import time

import httpx

from config.settings import get_settings


def _gemini_model_path(model: str) -> str:
    m = model.strip()
    if not m.startswith("models/"):
        m = f"models/{m}"
    return m


def embed_texts(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    if settings.openai_api_key:
        return _embed_openai(texts)
    if settings.google_api_key:
        return _embed_gemini(texts)
    raise RuntimeError("Set OPENAI_API_KEY or GOOGLE_API_KEY for embeddings.")


def _embed_openai(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    url = "https://api.openai.com/v1/embeddings"
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    body = {"model": settings.openai_embedding_model, "input": texts}
    with httpx.Client(timeout=60) as client:
        r = client.post(url, json=body, headers=headers)
        r.raise_for_status()
        data = r.json()["data"]
    return [d["embedding"] for d in sorted(data, key=lambda x: x["index"])]


def _embed_gemini(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    key = settings.google_api_key
    assert key
    model = _gemini_model_path(settings.gemini_embedding_model)
    out: list[list[float]] = []
    with httpx.Client(timeout=settings.gemini_embed_timeout_sec) as client:
        for t in texts:
            url = f"https://generativelanguage.googleapis.com/v1beta/{model}:embedContent"
            max_attempts = max(settings.gemini_embed_max_retries, 0) + 1
            last_detail = ""
            for attempt in range(max_attempts):
                r = client.post(
                    url,
                    params={"key": key},
                    json={"content": {"parts": [{"text": t}]}},
                )
                if r.status_code < 400:
                    payload = r.json()
                    emb = payload.get("embedding") or {}
                    vec = emb.get("values")
                    if not vec:
                        raise RuntimeError(f"Gemini embed unexpected response: {payload!r:.500}")
                    out.append(vec)
                    break

                detail = r.text.replace("\n", " ").strip()
                if len(detail) > 240:
                    detail = detail[:240] + "..."
                last_detail = detail
                is_retryable = r.status_code in (429, 500, 502, 503, 504)
                if is_retryable and attempt < max_attempts - 1:
                    wait_sec = settings.gemini_embed_backoff_sec * (2**attempt)
                    if wait_sec > 0:
                        time.sleep(wait_sec)
                    continue

                raise RuntimeError(
                    f"Gemini embed HTTP {r.status_code}: {detail}. "
                    f"If 404, set GEMINI_EMBEDDING_MODEL in .env (e.g. gemini-embedding-001) "
                    "or use OPENAI_API_KEY for embeddings only."
                )
            else:
                raise RuntimeError(f"Gemini embed failed after retries: {last_detail}")
    return out
