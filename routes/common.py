"""Shared helpers for routes: current user, login decorators, JSON helpers."""
from functools import wraps
from urllib.parse import urlparse

from flask import abort, g, jsonify, redirect, request, session, url_for, flash

import db


def jerr(message, status=400, **extra):
    return jsonify({"ok": False, "error": message, **extra}), status


def safe_next(target, fallback):
    """Only allow redirects to paths on this site."""
    if not target:
        return fallback
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/") or target.startswith("//"):
        return fallback
    return target


def current_user():
    """The logged-in student, or None. A mismatching session token means another device logged in."""
    if "user" in g:
        return g.user
    g.user = None
    g.signed_out_elsewhere = False
    uid = session.get("uid")
    if uid:
        user = db.one("SELECT * FROM users WHERE id=%s", (uid,))
        if user and user["session_token"] and user["session_token"] == session.get("token"):
            g.user = user
        elif user:
            g.signed_out_elsewhere = True
    return g.user


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            elsewhere = g.signed_out_elsewhere
            session.pop("uid", None)
            session.pop("token", None)
            if elsewhere:
                flash("You were signed out because this account was used to log in on another device.", "error")
            return redirect(url_for("auth.login_page", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapper


def api_login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return jerr("You have been signed out. If you logged in on another device, this session has ended.",
                        401, signed_out=True)
        return view(*args, **kwargs)
    return wrapper


def current_admin():
    if "admin" in g:
        return g.admin
    g.admin = None
    aid = session.get("admin_id")
    if aid:
        g.admin = db.one("SELECT id, username FROM admins WHERE id=%s", (aid,))
    return g.admin


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_admin() is None:
            return redirect(url_for("admin.login_page"))
        return view(*args, **kwargs)
    return wrapper


def get_or_404(row):
    if row is None:
        abort(404)
    return row
