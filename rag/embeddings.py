import httpx

from config.settings import get_settings


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
    """Uses Gemini embedding model; default dims 768 for text-embedding-004."""
    settings = get_settings()
    key = settings.google_api_key
    assert key
    model = "models/text-embedding-004"
    out: list[list[float]] = []
    with httpx.Client(timeout=60) as client:
        for t in texts:
            url = f"https://generativelanguage.googleapis.com/v1beta/{model}:embedContent"
            r = client.post(
                url,
                params={"key": key},
                json={"content": {"parts": [{"text": t}]}},
            )
            r.raise_for_status()
            vec = r.json()["embedding"]["values"]
            out.append(vec)
    return out
