"""Student authentication: register (Gmail OTP), login (Roll No. + password), forgot password."""
import secrets
import time

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import config
import db
import mailer
import schema
import security
from routes.common import current_user, jerr, safe_next
from services import exams

bp = Blueprint("auth", __name__)


def _lists():
    return (schema.get_setting("branches", config.DEFAULT_BRANCHES),
            schema.get_setting("years", config.DEFAULT_YEARS))


# ------------------------------------------------------------------ pages
@bp.get("/login")
def login_page():
    if current_user():
        return redirect(safe_next(request.args.get("next"), url_for("student.dashboard")))
    return render_template("auth/login.html", next=request.args.get("next", ""))


@bp.route("/register", methods=["GET", "POST"])
def register_page():
    if current_user():
        return redirect(url_for("student.dashboard"))
    branches, years = _lists()
    
    if request.method == "POST":
        # Direct form submission fallback handling
        name = security.clean_name(request.form.get("name"))
        roll = security.normalize_roll(request.form.get("roll"))
        branch = request.form.get("branch", "")
        year = request.form.get("year", "")
        email = security.normalize_email(request.form.get("email"))
        password = request.form.get("password", "")
        
        if not name or len(name) < 3:
            flash("Please enter your full name.", "error")
            return render_template("auth/register.html", branches=branches, years=years, dev=mailer.dev_mode()), 400
        if not security.is_valid_roll(roll):
            flash("Please enter a valid Roll No.", "error")
            return render_template("auth/register.html", branches=branches, years=years, dev=mailer.dev_mode()), 400
        if branch not in branches or year not in years:
            flash("Please choose your branch and year from the lists.", "error")
            return render_template("auth/register.html", branches=branches, years=years, dev=mailer.dev_mode()), 400
        problem = security.password_problem(password)
        if problem:
            flash(problem, "error")
            return render_template("auth/register.html", branches=branches, years=years, dev=mailer.dev_mode()), 400
        if db.one("SELECT id FROM users WHERE email=%s OR roll=%s", (email, roll)):
            flash("This email or Roll No. is already registered.", "error")
            return render_template("auth/register.html", branches=branches, years=years, dev=mailer.dev_mode()), 400
            
        try:
            user_id = db.insert(
                "INSERT INTO users(email, name, roll, branch, year, password_hash, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (email, name, roll, branch, year, security.hash_password(password), int(time.time())))
        except db.IntegrityError:
            flash("Registration failed due to a duplicate entry.", "error")
            return render_template("auth/register.html", branches=branches, years=years, dev=mailer.dev_mode()), 409
            
        start_session(db.one("SELECT * FROM users WHERE id=%s", (user_id,)))
        return redirect(url_for("student.dashboard"))

    return render_template("auth/register.html", branches=branches, years=years, dev=mailer.dev_mode())


@bp.get("/forgot")
def forgot_page():
    return render_template("auth/forgot.html", dev=mailer.dev_mode())


# ------------------------------------------------------------------ login / logout
def start_session(user):
    """Log the student in. A new login replaces any older session (one active device per account)."""
    previous = user["session_token"]
    token = secrets.token_urlsafe(24)
    db.update("UPDATE users SET session_token=%s, failed_logins=0, locked_until=0, last_login=%s WHERE id=%s",
              (token, int(time.time()), user["id"]))
    if previous:
        active = db.one("SELECT id FROM attempts WHERE user_id=%s AND submitted_at IS NULL AND deadline_at > %s",
                        (user["id"], int(time.time())))
        if active:
            exams.record_event(active["id"], "SECOND_LOGIN", "Account logged in on another device during the exam")
    session.clear()
    session["uid"] = user["id"]
    session["token"] = token
    session.permanent = True


@bp.post("/login")
def login_submit():
    roll = security.normalize_roll(request.form.get("roll"))
    password = request.form.get("password", "")
    nxt = request.form.get("next", "")

    def fail(message):
        flash(message, "error")
        return render_template("auth/login.html", next=nxt, roll=roll), 401

    if security.rate_limited(f"login:ip:{security.client_ip()}", 30, 600):
        return fail("Too many attempts from your network. Please wait a few minutes and try again.")
    user = db.one("SELECT * FROM users WHERE roll=%s", (roll,)) if roll else None
    now = int(time.time())
    if user and user["locked_until"] > now:
        minutes = (user["locked_until"] - now + 59) // 60
        return fail(f"Too many wrong passwords. This account is locked for {minutes} more minute(s).")
    if not user or not security.verify_password(user["password_hash"], password):
        if user:
            fails = user["failed_logins"] + 1
            locked = now + config.LOGIN_LOCK_SECONDS if fails >= config.LOGIN_MAX_FAILS else 0
            db.update("UPDATE users SET failed_logins=%s, locked_until=%s WHERE id=%s",
                      (0 if locked else fails, locked, user["id"]))
        return fail("Incorrect Roll No. or password.")
    start_session(user)
    return redirect(safe_next(nxt, url_for("student.dashboard")))


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login_page"))


# ------------------------------------------------------------------ OTP helpers
def _send_otp(email, purpose):
    if security.rate_limited(f"otp:ip:{security.client_ip()}", 12, 3600):
        return jerr("Too many requests from your network. Please try again later.", 429)
    try:
        code = security.issue_otp(email, purpose)
    except ValueError as e:
        return jerr(str(e), 429)
    try:
        result = mailer.send_otp(email, code, purpose)
    except mailer.MailError as e:
        security.cancel_otp(email, purpose)
        return jerr(str(e), 502)
    payload = {"ok": True, "cooldown": config.OTP_RESEND_SECONDS}
    if result["mode"] == "dev":
        payload["dev_otp"] = result["code"]
    return payload


def _verify(purpose, session_key):
    data = request.get_json(silent=True) or {}
    email = security.normalize_email(data.get("email"))
    ok, message = security.verify_otp(email, purpose, str(data.get("code", "")))
    if not ok:
        return jerr(message, 400)
    session[session_key] = {"email": email, "at": int(time.time())}
    return {"ok": True}


def _verified_email(session_key, max_age=900):
    info = session.get(session_key) or {}
    if info and time.time() - info.get("at", 0) <= max_age:
        return info["email"]
    return None


# ------------------------------------------------------------------ registration API
@bp.post("/api/register/send-otp")
def register_send_otp():
    email = security.normalize_email((request.get_json(silent=True) or {}).get("email"))
    if not security.is_gmail(email):
        return jerr("Please enter a valid Gmail address (example@gmail.com).")
    if db.one("SELECT id FROM users WHERE email=%s", (email,)):
        return jerr("This Gmail is already registered. Please log in with your Roll No. and password.", 409)
    return _send_otp(email, "register")


@bp.post("/api/register/verify-otp")
def register_verify_otp():
    return _verify("register", "reg")


@bp.post("/api/register/complete")
def register_complete():
    email = _verified_email("reg")
    if not email:
        return jerr("Your email verification has expired. Please start again.", 403)
    data = request.get_json(silent=True) or {}
    name = security.clean_name(data.get("name"))
    roll = security.normalize_roll(data.get("roll"))
    branch, year = data.get("branch", ""), data.get("year", "")
    password, confirm = data.get("password", ""), data.get("confirm", "")
    branches, years = _lists()
    if len(name) < 3 or len(name) > 100:
        return jerr("Please enter your full name.")
    if not security.is_valid_roll(roll):
        return jerr("Please enter a valid Roll No. (letters, numbers, - / _ . only).")
    if branch not in branches or year not in years:
        return jerr("Please choose your branch and year from the lists.")
    problem = security.password_problem(password)
    if problem:
        return jerr(problem)
    if password != confirm:
        return jerr("The two passwords do not match.")
    try:
        user_id = db.insert(
            "INSERT INTO users(email, name, roll, branch, year, password_hash, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (email, name, roll, branch, year, security.hash_password(password), int(time.time())))
    except db.IntegrityError as e:
        if "email" in str(e).lower():
            return jerr("This Gmail is already registered.", 409)
        return jerr("This Roll No. is already registered. If it is yours, log in or ask your admin.", 409)
    start_session(db.one("SELECT * FROM users WHERE id=%s", (user_id,)))
    return {"ok": True, "redirect": url_for("student.dashboard")}


# ------------------------------------------------------------------ forgot password API
@bp.post("/api/forgot/send-otp")
def forgot_send_otp():
    email = security.normalize_email((request.get_json(silent=True) or {}).get("email"))
    if not security.is_gmail(email):
        return jerr("Please enter the Gmail address you registered with.")
    if not db.one("SELECT id FROM users WHERE email=%s", (email,)):
        return jerr("No account was found for this Gmail. Please register first.", 404)
    return _send_otp(email, "reset")


@bp.post("/api/forgot/verify-otp")
def forgot_verify_otp():
    return _verify("reset", "reset")


@bp.post("/api/forgot/reset")
def forgot_reset():
    email = _verified_email("reset")
    if not email:
        return jerr("Your email verification has expired. Please start again.", 403)
    data = request.get_json(silent=True) or {}
    password, confirm = data.get("password", ""), data.get("confirm", "")
    problem = security.password_problem(password)
    if problem:
        return jerr(problem)
    if password != confirm:
        return jerr("The two passwords do not match.")
    db.update("UPDATE users SET password_hash=%s, failed_logins=0, locked_until=0, session_token=NULL WHERE email=%s",
              (security.hash_password(password), email))
    session.pop("reset", None)
    return {"ok": True, "redirect": url_for("auth.login_page")}