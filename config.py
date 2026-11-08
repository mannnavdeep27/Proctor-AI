"""Application configuration.

Everything is read from environment variables. For local development you can put the
values in a `.env` file next to this file (see `.env.example`); on Vercel set them in
Project Settings -> Environment Variables.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv():
    """Tiny .env loader (no extra dependency). Real environment variables always win."""
    path = BASE_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def env(name, default=""):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


APP_NAME = "ProctorAI"
APP_ENV = env("APP_ENV", "development").lower()          # set to "production" on Vercel
IS_PROD = APP_ENV == "production"

SECRET_KEY = env("SECRET_KEY", "" if IS_PROD else "dev-only-secret-change-me")

# ---------------------------------------------------------------- database (MySQL / MariaDB)
DB_HOST = env("DB_HOST", "127.0.0.1")
DB_PORT = int(env("DB_PORT", "3306"))
DB_USER = env("DB_USER", "root")
DB_PASSWORD = env("DB_PASSWORD", "")                    # XAMPP default: empty password
DB_NAME = env("DB_NAME", "proctorai")
DB_SSL = env_bool("DB_SSL", False)                       # most hosted MySQL services need True
DB_SSL_CA = env("DB_SSL_CA", "")                         # optional path to a CA bundle

# ---------------------------------------------------------------- admin bootstrap
# The first admin account is created from these values. In production you MUST set both.
ADMIN_USERNAME = env("ADMIN_USERNAME", "" if IS_PROD else "admin")
ADMIN_PASSWORD = env("ADMIN_PASSWORD", "" if IS_PROD else "admin123")

# ---------------------------------------------------------------- email (OTP)
# Use ONE of these. If none is set, development mode prints/shows the OTP instead of emailing.
MAIL_FROM_EMAIL = env("MAIL_FROM_EMAIL")                 # sender address (must be verified with the provider)
MAIL_FROM_NAME = env("MAIL_FROM_NAME", APP_NAME)
BREVO_API_KEY = env("BREVO_API_KEY")                     # https://www.brevo.com  (free plan, verify a sender)
RESEND_API_KEY = env("RESEND_API_KEY")                   # https://resend.com     (needs a verified domain)
SMTP_HOST = env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(env("SMTP_PORT", "465"))
SMTP_USER = env("SMTP_USER")                             # e.g. yourname@gmail.com
SMTP_PASSWORD = env("SMTP_PASSWORD")                     # Gmail App Password

# ---------------------------------------------------------------- security limits
OTP_VALID_SECONDS = 5 * 60
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_SECONDS = 30
OTP_MAX_SENDS_PER_HOUR = 6
LOGIN_MAX_FAILS = 5
LOGIN_LOCK_SECONDS = 10 * 60
SESSION_HOURS = 12
MIN_PASSWORD_LENGTH = 6

# ---------------------------------------------------------------- exam / risk
RISK_POINTS = {
    "SESSION_START": 0,
    "SESSION_END": 0,
    "AWAY_SHORT": 1,        # left the window but came back within the grace time
    "AWAY_LONG": 25,        # stayed away longer than the grace time (a violation)
    "COPY": 3,
    "CUT": 3,
    "CONTEXT_MENU": 2,
    "DEVTOOLS_KEY": 5,
    "SECOND_LOGIN": 15,     # same account logged in on another device during the exam
}
EVENT_LABELS = {
    "SESSION_START": "Exam started",
    "SESSION_END": "Exam ended",
    "AWAY_SHORT": "Left the window briefly",
    "AWAY_LONG": "Left the exam window",
    "COPY": "Copy attempt",
    "CUT": "Cut attempt",
    "CONTEXT_MENU": "Right-click attempt",
    "DEVTOOLS_KEY": "Developer-tools shortcut",
    "SECOND_LOGIN": "Logged in on another device",
}
VERDICTS = [(20, "CLEAN"), (50, "SUSPICIOUS"), (float("inf"), "HIGH RISK")]

END_REASONS = {
    "SUBMITTED": "Submitted by the student",
    "TIME_UP": "Time ran out (auto-submitted)",
    "WINDOW_SWITCH": "Auto-submitted: left the exam window",
}

DEFAULT_BRANCHES = ["CSE", "IT", "AI & ML", "Data Science", "Cyber Security", "ECE", "EEE", "Mechanical", "Civil", "Other"]
DEFAULT_YEARS = ["1st Year", "2nd Year", "3rd Year", "4th Year"]

DEFAULT_RULES = [
    "Use only one device and keep this exam window in front for the whole exam.",
    "Do not refresh, close or leave the page while the exam is running.",
    "Do not use other people, books, notes or websites unless the exam says you may.",
    "Your answers are saved automatically, but you must press Submit when you finish.",
    "All activity during the exam is recorded. Suspicious activity can lead to your result being cancelled.",
]
