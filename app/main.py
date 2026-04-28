import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.email_transcript import send_chat_transcript
from app.handlers import handle_chat
from app.partner import is_partner_session
from app.partner_assets import resolve_asset_file
from app.web_search import web_search_configured
from config.settings import get_settings


def _redact_secrets(text: str) -> str:
    t = text
    t = re.sub(r"sk-[a-zA-Z0-9]{20,}", "[REDACTED]", t)
    t = re.sub(r"AIza[0-9A-Za-z\-_]{30,}", "[REDACTED]", t)
    return t


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
        "process_cwd": os.getcwd(),
        "repo_root": str(Path(__file__).resolve().parents[1]),
    }


@app.on_event("startup")
def _log_config_on_startup() -> None:
    s = get_settings()
    logging.getLogger("uvicorn.error").info(
        "ASLAN: LLM_PROVIDER=%s GEMINI_CHAT_MODEL=%s (open .env under repo root, not cwd)",
        s.llm_provider,
        s.gemini_chat_model,
    )


@app.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest) -> ChatResponse:
    try:
        result = handle_chat(
            body.message,
            body.region_hint,
            partner_token=body.partner_token,
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
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
