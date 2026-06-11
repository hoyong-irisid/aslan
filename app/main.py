import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.email_transcript import send_chat_transcript
from app.handlers import handle_chat
from app.partner import is_partner_session, partner_enabled
from app.partner_assets import resolve_asset_file
from app.partner_db import init_db
from app.partner_routes import router as partner_registry_router
from app.web_search import web_search_configured
from config.settings import get_settings


def _redact_secrets(text: str) -> str:
    t = text
    t = re.sub(r"sk-[a-zA-Z0-9]{20,}", "[REDACTED]", t)
    t = re.sub(r"AIza[0-9A-Za-z\-_]{30,}", "[REDACTED]", t)
    return t


def _mask_token(token: str | None) -> str:
    t = (token or "").strip()
    if not t:
        return "none"
    if len(t) <= 8:
        return "***"
    return f"{t[:4]}...{t[-4:]}"


class ChatHistoryLine(BaseModel):
    role: str = Field(min_length=1)
    text: str = Field(default="")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    region_hint: str | None = Field(
        default=None,
        description="Optional ISO-3166 alpha-2 country for regional contacts (e.g. AE, ID).",
    )
    partner_token: str | None = Field(
        default=None,
        description="Session token issued after a valid partner access code was verified.",
    )
    chat_history: list[ChatHistoryLine] = Field(
        default_factory=list,
        description="Recent turns from this chat session (oldest first).",
    )


class ChatResponse(BaseModel):
    reply: str
    partner_authenticated: bool = False
    partner_token: str | None = None


class ChatTranscriptLine(BaseModel):
    role: str = Field(min_length=1)
    text: str = Field(default="")


class EmailTranscriptRequest(BaseModel):
    partner_token: str | None = None
    to_email: str = Field(min_length=3)
    messages: list[ChatTranscriptLine] = Field(default_factory=list)


class EmailTranscriptResponse(BaseModel):
    ok: bool = True
    detail: str | None = None


app = FastAPI(title="ASLAN Chat API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_WIDGET_DIR = Path(__file__).resolve().parents[1] / "widget"
if not _WIDGET_DIR.is_dir():
    logging.getLogger("uvicorn.error").warning("Widget dir missing: %s", _WIDGET_DIR)

app.mount(
    "/widget",
    StaticFiles(directory=str(_WIDGET_DIR), html=True),
    name="widget",
)

_PARTNER_DIR = Path(__file__).resolve().parents[1] / "partner"
_PARTNER_UI_VERSION = "2026-06-11-v4"
_PARTNER_ADMIN_PATH = "/partner/manage"
_NO_CACHE_HTML = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _partner_html(filename: str) -> FileResponse:
    path = _PARTNER_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Partner page not found: {filename}")
    return FileResponse(
        path=str(path),
        media_type="text/html; charset=utf-8",
        headers={**_NO_CACHE_HTML, "X-Partner-UI-Version": _PARTNER_UI_VERSION},
    )


def _partner_redirect(url: str) -> RedirectResponse:
    return RedirectResponse(
        url=url,
        status_code=302,
        headers=_NO_CACHE_HTML,
    )


if _PARTNER_DIR.is_dir():

    @app.get(_PARTNER_ADMIN_PATH, include_in_schema=False)
    def partner_admin_page() -> FileResponse:
        """Canonical admin URL (avoid stale CDN/Apache cache on /partner/admin.html)."""
        return _partner_html("admin.html")

    @app.get("/partner/admin.html", include_in_schema=False)
    def partner_admin_page_legacy() -> RedirectResponse:
        return _partner_redirect(_PARTNER_ADMIN_PATH)

    @app.get("/partner/register.html", include_in_schema=False)
    def partner_register_page() -> FileResponse:
        return _partner_html("register.html")

    @app.get("/partner/", include_in_schema=False)
    @app.get("/partner", include_in_schema=False)
    def partner_portal_index() -> FileResponse:
        return _partner_html("index.html")

app.include_router(partner_registry_router)


@app.middleware("http")
async def _no_cache_partner_api(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/partner") or path.startswith("/partner/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/widget/")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/config")
def health_config() -> dict[str, Any]:
    """Which LLM model the server loaded (no secrets)."""
    s = get_settings()
    return {
        "llm_provider": s.llm_provider,
        "gemini_chat_model": s.gemini_chat_model,
        "openai_chat_model": s.openai_chat_model,
        "aslan_gemini_agent": s.aslan_gemini_agent,
        "web_search_configured": web_search_configured(s),
        "web_search_provider": s.web_search_provider,
        "email_transcript_configured": bool(
            (s.resend_api_key and s.resend_from)
            or (s.smtp_host and s.smtp_from)
        ),
        "partner_auth_enabled": partner_enabled(),
        "partner_registry_enabled": bool((s.partner_admin_api_key or "").strip())
        or bool((s.resend_api_key and s.resend_from) or (s.smtp_host and s.smtp_from)),
        "process_cwd": os.getcwd(),
        "repo_root": str(Path(__file__).resolve().parents[1]),
        ** _partner_registry_health(),
        "partner_ui_version": _PARTNER_UI_VERSION,
        "partner_admin_url": _PARTNER_ADMIN_PATH,
        "partner_admin_html": _partner_portal_file_info("admin.html"),
    }


def _partner_portal_file_info(filename: str) -> dict[str, Any] | None:
    path = _PARTNER_DIR / filename
    if not path.is_file():
        return None
    st = path.stat()
    return {
        "path": str(path),
        "size_bytes": st.st_size,
        "mtime_unix": int(st.st_mtime),
    }


def _partner_registry_health() -> dict[str, Any]:
    try:
        from app.partner_db import admin_stats, partner_db_path

        return {
            "partner_db_path": str(partner_db_path()),
            "partner_stats": admin_stats(),
        }
    except Exception as exc:
        return {"partner_db_error": exc.__class__.__name__}


@app.on_event("startup")
def _log_config_on_startup() -> None:
    init_db()
    s = get_settings()
    log = logging.getLogger("uvicorn.error")
    log.info(
        "ASLAN: LLM_PROVIDER=%s GEMINI_CHAT_MODEL=%s (open .env under repo root, not cwd)",
        s.llm_provider,
        s.gemini_chat_model,
    )
    try:
        from app.partner_db import admin_dashboard_data, partner_db_path

        dash = admin_dashboard_data(include_inactive=True)
        log.info(
            "Partner DB: path=%s partners=%s active=%s",
            partner_db_path(),
            dash["stats"]["total_partners_ever"],
            dash["stats"]["active_partners"],
        )
    except Exception as exc:
        log.warning("Partner DB startup check failed: %s", exc)


@app.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest) -> ChatResponse:
    try:
        result = handle_chat(
            body.message,
            body.region_hint,
            partner_token=body.partner_token,
            chat_history=body.chat_history,
        )
    except Exception as exc:
        return ChatResponse(
            reply=_redact_secrets(
                f"Internal error: {exc.__class__.__name__}: {str(exc)}"
            ),
            partner_authenticated=False,
            partner_token=body.partner_token,
        )
    return ChatResponse(
        reply=result.reply,
        partner_authenticated=result.partner_authenticated,
        partner_token=result.partner_token,
    )


@app.get("/partner/asset/{asset_id}")
def partner_asset(asset_id: str, token: str = Query(default="")) -> FileResponse:
    if not is_partner_session(token):
        raise HTTPException(status_code=403, detail="Partner authentication required")
    path = resolve_asset_file(asset_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(path=str(path))


@app.post("/email/chat-transcript", response_model=EmailTranscriptResponse)
def email_chat_transcript(body: EmailTranscriptRequest) -> EmailTranscriptResponse:
    if not is_partner_session(body.partner_token):
        raise HTTPException(status_code=403, detail="Partner authentication required")
    if not body.messages:
        raise HTTPException(status_code=400, detail="No messages to send")
    if len(body.messages) > 300:
        raise HTTPException(status_code=400, detail="Too many messages (max 300)")
    settings = get_settings()
    log = logging.getLogger("uvicorn.error")
    event_ts = datetime.now(timezone.utc).isoformat()
    token_hint = _mask_token(body.partner_token)
    lines: list[str] = []
    total = 0
    for m in body.messages:
        r = (m.role or "").strip().lower()
        if r not in ("user", "assistant"):
            raise HTTPException(status_code=400, detail=f"Invalid role: {m.role!r}")
        label = "User" if r == "user" else "Assistant"
        t = m.text or ""
        total += len(t)
        if total > 400_000:
            raise HTTPException(status_code=400, detail="Transcript too large")
        lines.append(f"{label}:\n{t.strip()}")
    try:
        send_chat_transcript(
            to_email=body.to_email,
            transcript_lines=lines,
            settings=settings,
        )
    except ValueError as exc:
        log.warning(
            "Transcript email failed validation ts=%s token=%s to=%s detail=%s",
            event_ts,
            token_hint,
            body.to_email,
            exc,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        log.warning(
            "Transcript email failed runtime ts=%s token=%s to=%s detail=%s",
            event_ts,
            token_hint,
            body.to_email,
            exc,
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        log.exception(
            "Transcript email failed unexpected ts=%s token=%s to=%s type=%s",
            event_ts,
            token_hint,
            body.to_email,
            exc.__class__.__name__,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send email: {exc.__class__.__name__}",
        ) from exc
    return EmailTranscriptResponse(ok=True, detail="Transcript sent")


def run() -> None:
    settings = get_settings()
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.aslan_api_host,
        port=settings.aslan_api_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
