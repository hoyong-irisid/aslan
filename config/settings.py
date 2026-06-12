from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Always load `.env` next to the repo root, not from whatever the shell cwd is.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    aslan_api_host: str = "0.0.0.0"
    aslan_api_port: int = 8000

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "iris_docs"
    # Separate collection for partner-only documents (manuals not allowed on public site).
    qdrant_collection_partner: str = "iris_partner"

    # Comma-separated partner access codes (e.g. "1234,ABCD-2025").
    # Kept as raw string so we can re-read at runtime without restarting.
    partner_codes: str = ""
    partner_session_ttl_minutes: int = 120
    partner_db_path: str = "data/partners.db"
    partner_admin_api_key: str | None = None
    partner_otp_ttl_minutes: int = 10
    # Partner corpus lives outside this repo (not committed). Absolute path, or path relative to repo root.
    # If unset, defaults to ../aslan-rag/partner_docs (sibling of the aslan checkout).
    partner_docs_dir: str | None = None

    google_api_key: str | None = None
    openai_api_key: str | None = None

    @field_validator(
        "google_api_key",
        "openai_api_key",
        "qdrant_api_key",
        "tavily_api_key",
        "serper_api_key",
        "google_cse_api_key",
        "google_cse_id",
        "partner_docs_dir",
        "smtp_host",
        "smtp_user",
        "smtp_password",
        "smtp_from",
        "resend_api_key",
        "resend_from",
        "partner_admin_api_key",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, v: object) -> object:
        if v == "":
            return None
        return v

    @field_validator("gemini_embedding_model", mode="before")
    @classmethod
    def _gemini_embed_model_not_retired(cls, v: object) -> object:
        if not isinstance(v, str):
            return v
        s = v.strip()
        if s in ("text-embedding-004", "models/text-embedding-004"):
            return "gemini-embedding-001"
        return s
    openai_embedding_model: str = "text-embedding-3-small"
    openai_chat_model: str = "gpt-4o-mini"

    llm_provider: str = "gemini"  # gemini | openai
    # gemini-2.0-flash is blocked for *new* AI Studio keys (404). Use a current model from ListModels.
    gemini_chat_model: str = "gemini-2.5-flash"
    # text-embedding-004 is retired for many keys (404). Use gemini-embedding-001 (see .env.example).
    gemini_embedding_model: str = "gemini-embedding-001"

    # When LLM_PROVIDER=gemini: use function-calling agent (search tool + general answers). Set false for legacy router+RAG path.
    aslan_gemini_agent: bool = True

    # Web search tool (Gemini agent): auto | tavily | serper | google_cse | none
    web_search_provider: str = "auto"
    tavily_api_key: str | None = None
    serper_api_key: str | None = None
    google_cse_api_key: str | None = None
    google_cse_id: str | None = None
    web_search_max_results: int = 3
    web_search_timeout_sec: float = 12.0
    gemini_request_timeout_sec: float = 60.0
    gemini_max_tool_rounds: int = 3
    gemini_embed_timeout_sec: float = 30.0
    gemini_embed_max_retries: int = 3
    gemini_embed_backoff_sec: float = 1.0

    rag_search_top_k: int = 3
    rag_final_top_k: int = 2
    rag_min_score: float = 0.15
    # Minimum similarity to attach prefetch KB to Gemini agent (below rag_min_score).
    rag_prefetch_min_score: float = 0.05
    chunk_size_tokens: int = 400
    chunk_overlap_tokens: int = 60

    # Optional: chat transcript email — Resend (HTTPS API, works from localhost) or SMTP.
    resend_api_key: str | None = None
    resend_from: str | None = None  # e.g. "Iris ID <noreply@irisid.com>"

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_transcript_subject: str = "ASLAN — IRIS ID chat transcript"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def resolve_partner_docs_path(settings: Settings | None = None) -> Path:
    """Directory for partner-only source files (PDF/txt). Kept outside git by default."""
    s = settings or get_settings()
    raw = (s.partner_docs_dir or "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p.resolve() if p.is_absolute() else (_REPO_ROOT / p).resolve()
    return (_REPO_ROOT.parent / "aslan-rag" / "partner_docs").resolve()
