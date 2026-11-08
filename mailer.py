"""Send OTP emails.

Provider priority: Brevo API -> Resend API -> SMTP (e.g. Gmail App Password).
If nothing is configured and APP_ENV is not "production", we run in DEV MODE:
the OTP is printed to the server console and returned to the page instead of being emailed.
"""
import json
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

import config


class MailError(RuntimeError):
    pass


def provider():
    if config.BREVO_API_KEY and config.MAIL_FROM_EMAIL:
        return "brevo"
    if config.RESEND_API_KEY and config.MAIL_FROM_EMAIL:
        return "resend"
    if config.SMTP_USER and config.SMTP_PASSWORD:
        return "smtp"
    return None


def dev_mode():
    return provider() is None and not config.IS_PROD


def _post_json(url, headers, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "ProctorAI/1.0", **headers})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:200]
        raise MailError(f"Email service error ({e.code}): {detail}")
    except (urllib.error.URLError, OSError) as e:
        raise MailError(f"Could not reach the email service: {e}")


def _send(to_addr, subject, text, html):
    name = provider()
    if name == "brevo":
        _post_json("https://api.brevo.com/v3/smtp/email", {"api-key": config.BREVO_API_KEY}, {
            "sender": {"email": config.MAIL_FROM_EMAIL, "name": config.MAIL_FROM_NAME},
            "to": [{"email": to_addr}], "subject": subject, "htmlContent": html, "textContent": text})
    elif name == "resend":
        _post_json("https://api.resend.com/emails", {"Authorization": f"Bearer {config.RESEND_API_KEY}"}, {
            "from": f"{config.MAIL_FROM_NAME} <{config.MAIL_FROM_EMAIL}>",
            "to": [to_addr], "subject": subject, "html": html, "text": text})
    elif name == "smtp":
        sender = config.MAIL_FROM_EMAIL or config.SMTP_USER
        msg = EmailMessage()
        msg["From"] = f"{config.MAIL_FROM_NAME} <{sender}>"
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        try:
            with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ssl.create_default_context(),
                                  timeout=20) as smtp:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD.replace(" ", ""))
                smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            raise MailError("Email login failed. Check SMTP_USER and use a Gmail App Password.")
        except (smtplib.SMTPException, OSError) as e:
            raise MailError(f"Could not send the email: {e}")
    else:
        raise MailError("Email is not configured on the server.")


def send_otp(to_addr, code, purpose):
    """Returns {"mode": "email"} or, in dev mode, {"mode": "dev", "code": code}."""
    if dev_mode():
        print(f"[DEV MODE] {purpose} OTP for {to_addr}: {code}", flush=True)
        return {"mode": "dev", "code": code}
    minutes = config.OTP_VALID_SECONDS // 60
    what = "registration" if purpose == "register" else "password reset"
    subject = f"{config.APP_NAME}: your verification code is {code}"
    text = (f"Your {config.APP_NAME} {what} code is {code}.\n\nIt is valid for {minutes} minutes. "
            f"Do not share it with anyone. If you did not request it, ignore this email.")
    html = (f"<div style='font-family:Arial,sans-serif;max-width:420px;margin:auto;padding:24px;border:1px solid #e5e7eb;"
            f"border-radius:12px'><h2 style='color:#4f46e5;margin:0 0 8px'>{config.APP_NAME}</h2>"
            f"<p style='color:#374151'>Your {what} code is:</p>"
            f"<p style='font-size:32px;letter-spacing:8px;font-weight:700;color:#111827;margin:12px 0'>{code}</p>"
            f"<p style='color:#6b7280;font-size:13px'>Valid for {minutes} minutes. Do not share it with anyone. "
            f"If you did not request this, ignore this email.</p></div>")
    _send(to_addr, subject, text, html)
    return {"mode": "email"}


def send_test(to_addr):
    if provider() is None:
        raise MailError("No email provider is configured.")
    _send(to_addr, f"{config.APP_NAME} test email", "Your email setup works.", "<p>Your email setup works.</p>")
