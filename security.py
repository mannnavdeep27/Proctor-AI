"""Passwords, OTPs, CSRF tokens, rate limiting and small validation helpers."""
import hashlib
import hmac
import re
import secrets
import time

from flask import request, session
from werkzeug.security import check_password_hash, generate_password_hash

import config
import db

GMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@gmail\.com$")
ROLL_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-_/\.]{0,58}$")


# ------------------------------------------------------------------ validation
def normalize_email(email):
    return (email or "").strip().lower()


def is_gmail(email):
    return bool(GMAIL_RE.match(normalize_email(email)))


def normalize_roll(roll):
    return (roll or "").strip().upper()


def is_valid_roll(roll):
    return bool(ROLL_RE.match(normalize_roll(roll)))


def clean_name(name):
    return " ".join((name or "").split())


def password_problem(password):
    """Return an error message, or None when the password is acceptable."""
    if len(password or "") < config.MIN_PASSWORD_LENGTH:
        return f"Password must be at least {config.MIN_PASSWORD_LENGTH} characters long."
    if len(password) > 128:
        return "Password is too long."
    return None


# ------------------------------------------------------------------ passwords
def hash_password(password):
    return generate_password_hash(password, method="pbkdf2:sha256")


def verify_password(password_hash, password):
    return check_password_hash(password_hash, password or "")


# ------------------------------------------------------------------ CSRF
def csrf_token():
    token = session.get("csrf")
    if not token:
        token = session["csrf"] = secrets.token_urlsafe(24)
    return token


def csrf_ok():
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    expected = session.get("csrf") or ""
    return bool(expected) and hmac.compare_digest(sent, expected)


# ------------------------------------------------------------------ rate limiting (stored in MySQL)
def client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[0].strip() or request.remote_addr or "unknown")[:60]


def rate_limited(key, limit, window_seconds):
    """Count one hit for `key`. Returns True when the caller is over the limit."""
    now = int(time.time())
    row = db.one("SELECT hits, reset_at FROM rate_limits WHERE k=%s", (key,))
    if row is None or row["reset_at"] <= now:
        db.execute("DELETE FROM rate_limits WHERE k=%s", (key,))
        try:
            db.insert("INSERT INTO rate_limits(k, hits, reset_at) VALUES (%s, 1, %s)", (key, now + window_seconds))
        except db.IntegrityError:
            pass
        return False
    db.update("UPDATE rate_limits SET hits = hits + 1 WHERE k=%s", (key,))
    return row["hits"] + 1 > limit


# ------------------------------------------------------------------ OTP
def _digest(code, salt):
    return hashlib.sha256((salt + code).encode()).hexdigest()


def issue_otp(email, purpose):
    """Create and store a 6-digit OTP. Raises ValueError with a user-friendly message when throttled."""
    email = normalize_email(email)
    now = int(time.time())
    row = db.one("SELECT * FROM otps WHERE email=%s AND purpose=%s", (email, purpose))
    sends, window_start = 1, now
    if row:
        wait = config.OTP_RESEND_SECONDS - (now - row["last_sent_at"])
        if wait > 0:
            raise ValueError(f"Please wait {wait} seconds before requesting another OTP.")
        if now - row["window_start"] < 3600:
            if row["sends"] >= config.OTP_MAX_SENDS_PER_HOUR:
                raise ValueError("Too many OTP requests. Please try again in an hour.")
            sends, window_start = row["sends"] + 1, row["window_start"]
    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(4)
    db.execute("DELETE FROM otps WHERE email=%s AND purpose=%s", (email, purpose))
    db.insert(
        "INSERT INTO otps(email, purpose, code_hash, salt, expires_at, attempts, last_sent_at, sends, window_start) "
        "VALUES (%s,%s,%s,%s,%s,0,%s,%s,%s)",
        (email, purpose, _digest(code, salt), salt, now + config.OTP_VALID_SECONDS, now, sends, window_start))
    return code


def cancel_otp(email, purpose):
    db.execute("DELETE FROM otps WHERE email=%s AND purpose=%s", (normalize_email(email), purpose))


def verify_otp(email, purpose, code):
    """Returns (ok, message). A correct code is single-use."""
    email = normalize_email(email)
    row = db.one("SELECT * FROM otps WHERE email=%s AND purpose=%s", (email, purpose))
    if not row:
        return False, "Please request an OTP first."
    if time.time() > row["expires_at"]:
        cancel_otp(email, purpose)
        return False, "This OTP has expired. Please request a new one."
    if row["attempts"] >= config.OTP_MAX_ATTEMPTS:
        cancel_otp(email, purpose)
        return False, "Too many wrong attempts. Please request a new OTP."
    db.update("UPDATE otps SET attempts = attempts + 1 WHERE id=%s", (row["id"],))
    if hmac.compare_digest(_digest((code or "").strip(), row["salt"]), row["code_hash"]):
        cancel_otp(email, purpose)
        return True, "Verified"
    left = config.OTP_MAX_ATTEMPTS - row["attempts"] - 1
    return False, f"Incorrect OTP. {max(left, 0)} attempt(s) left."
