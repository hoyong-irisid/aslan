"""Partner signup OTP and access-code emails via Resend/SMTP."""

from __future__ import annotations

import httpx

from config.settings import Settings


def _send_simple_email(*, to_email: str, subject: str, text: str, settings: Settings) -> None:
    if settings.resend_api_key and settings.resend_from:
        payload = {
            "from": settings.resend_from.strip(),
            "to": [to_email.strip()],
            "subject": subject,
            "text": text,
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
            raise RuntimeError(f"Resend API HTTP {r.status_code}: {r.text[:300]}")
        return

    if settings.smtp_host and settings.smtp_from:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = to_email.strip()
        msg.set_content(text)
        if settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=45) as smtp:
                if settings.smtp_user and settings.smtp_password:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=45) as smtp:
                if settings.smtp_use_tls:
                    smtp.starttls()
                if settings.smtp_user and settings.smtp_password:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
        return

    raise RuntimeError(
        "Email is not configured. Set RESEND_API_KEY + RESEND_FROM or SMTP_* in .env"
    )


def send_partner_otp_email(*, to_email: str, otp: str, settings: Settings) -> None:
    text = (
        "Your verification code is:\n\n"
        f"  {otp}\n\n"
        "This code expires in 10 minutes.\n"
        "If you did not request partner access, ignore this email."
    )
    _send_simple_email(
        to_email=to_email,
        subject="Iris ID - Verify Your Email",
        text=text,
        settings=settings,
    )


def send_partner_code_email(
    *,
    to_email: str,
    name: str,
    code: str,
    settings: Settings,
) -> None:
    text = (
        f"Hello {name},\n\n"
        "Welcome to the IRIS ID partner program.\n\n"
        "Your Iris ID chatbot partner access code is:\n\n"
        f"  {code}\n\n"
        "Use this code in the Iris ID Assistant chat (Partner button) to unlock "
        "partner-only product manuals and technical content.\n\n"
        "Keep this code confidential.\n\n"
        "— IRIS ID"
    )
    _send_simple_email(
        to_email=to_email,
        subject="Iris ID - Your Partner Access Code",
        text=text,
        settings=settings,
    )
