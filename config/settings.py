from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    aslan_api_host: str = "0.0.0.0"
    aslan_api_port: int = 8000

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "iris_docs"

    google_api_key: str | None = None
    openai_api_key: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"
    openai_chat_model: str = "gpt-4o-mini"

    llm_provider: str = "gemini"  # gemini | openai
    gemini_chat_model: str = "gemini-2.0-flash"

    rag_search_top_k: int = 5
    rag_final_top_k: int = 3
    rag_min_score: float = 0.2
    chunk_size_tokens: int = 400
    chunk_overlap_tokens: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
