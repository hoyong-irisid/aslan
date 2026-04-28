"""Send chat transcripts via Resend API or SMTP (optional)."""

from __future__ import annotations

import base64
import re
import smtplib
from email.message import EmailMessage

import httpx

from config.settings import Settings


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_recipient(email: str) -> bool:
    return bool(_EMAIL_RE.match((email or "").strip()))


def _build_body(transcript_lines: list[str]) -> tuple[str, str]:
    body = "\n\n".join(transcript_lines).strip() or "(empty transcript)"
    subject_line = "ASLAN — IRIS ID chat transcript"
    return body, subject_line


def _send_via_resend(
    *,
    to_email: str,
    body: str,
    subject: str,
    settings: Settings,
) -> None:
    from_addr = (settings.resend_from or "").strip()
    if not from_addr:
        raise RuntimeError(
            "RESEND_FROM is required when RESEND_API_KEY is set (e.g. IRIS ID <onboarding@resend.dev>)."
        )
    text_intro = (
        "Your IRIS ID assistant chat transcript is attached as a text file.\n\n"
        "---\n\n"
        + body
    )
    b64 = base64.b64encode(body.encode("utf-8")).decode("ascii")
    payload: dict = {
        "from": from_addr,
        "to": [to_email.strip()],
        "subject": subject,
        "text": text_intro,
        "attachments": [
            {
                "filename": "aslan-chat-transcript.txt",
                "content": b64,
            }
        ],
    }
    with httpx.Client(timeout=45.0) as client:
        r = client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if r.status_code >= 400:
        detail = r.text.replace("\n", " ").strip()
        if len(detail) > 400:
            detail = detail[:400] + "..."
        raise RuntimeError(f"Resend API HTTP {r.status_code}: {detail}")


def _send_via_smtp(
    *,
    to_email: str,
    body: str,
    subject: str,
    settings: Settings,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to_email.strip()
    msg.set_content(
        "Your IRIS ID assistant chat transcript is attached as a text file.\n\n"
        "---\n\n"
        + body
    )
    msg.add_attachment(
        body.encode("utf-8"),
        maintype="text",
        subtype="plain",
        filename="aslan-chat-transcript.txt",
    )

    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as smtp:
            if settings.smtp_user and settings.smtp_password:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_user and settings.smtp_password:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)


def send_chat_transcript(
    *,
    to_email: str,
    transcript_lines: list[str],
    settings: Settings,
) -> None:
    if not validate_recipient(to_email):
        raise ValueError("Invalid email address")

    body, _ = _build_body(transcript_lines)
    subject = settings.smtp_transcript_subject or "ASLAN — IRIS ID chat transcript"

    if settings.resend_api_key:
        _send_via_resend(to_email=to_email, body=body, subject=subject, settings=settings)
        return

    if settings.smtp_host and settings.smtp_from:
        _send_via_smtp(to_email=to_email, body=body, subject=subject, settings=settings)
        return

    raise RuntimeError(
        "Email is not configured. Set RESEND_API_KEY + RESEND_FROM, "
        "or SMTP_HOST + SMTP_FROM in .env (see .env.example)."
    )
