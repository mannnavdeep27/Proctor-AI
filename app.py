"""ProctorAI web application (Flask).

Local run:       python app.py
Vercel:          api/index.py imports `app` from this file.
"""
import secrets
import sys
from datetime import datetime, timedelta, timezone

import pymysql
from flask import Flask, g, render_template, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

import config
import db
import schema
import security

CSP = ("default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def config_problems():
    problems = []
    if config.IS_PROD and not config.SECRET_KEY:
        problems.append("SECRET_KEY is not set.")
    return problems


def create_app():
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY or secrets.token_hex(16)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.IS_PROD,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=config.SESSION_HOURS),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        JSON_SORT_KEYS=False,
    )
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Initialize database schema once at application startup
    with app.app_context():
        schema.init_db()

    from routes import admin, auth, exam_api, student
    for module in (auth, student, exam_api, admin):
        app.register_blueprint(module.bp)

    # ------------------------------------------------------------ template helpers
    @app.template_filter("utcfmt")
    def utcfmt(ts):
        if not ts:
            return "-"
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    @app.template_global("icon")
    def icon(name, cls=""):
        return app.jinja_env.get_template("_icons.html").module.icon(name, cls)

    @app.template_filter("fromjson")
    def fromjson(raw):
        import json
        try:
            return json.loads(raw) if raw else []
        except ValueError:
            return []

    @app.context_processor
    def inject():
        from routes.common import current_admin, current_user
        return {"csrf_token": security.csrf_token, "app_name": config.APP_NAME,
                "me": current_user(), "admin_user": current_admin()}

    # ------------------------------------------------------------ request lifecycle
    @app.before_request
    def before():
        if request.endpoint == "static":
            return None
        problems = config_problems()
        if problems:
            return render_template("message.html", title="Server is not configured",
                                   text=" ".join(problems) + " Set it in the hosting environment variables.",
                                   tone="warn"), 500
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and not security.csrf_ok():
            if request.path.startswith("/api/"):
                return {"ok": False, "error": "Your session expired. Please refresh the page."}, 400
            return render_template("message.html", title="Session expired",
                                   text="Your session expired. Please go back, refresh the page and try again.",
                                   tone="warn"), 400
        return None

    @app.after_request
    def after(resp):
        if resp.status_code < 500:
            try:
                db.commit()
            except pymysql.MySQLError:
                db.rollback()
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = CSP
        if request.endpoint != "static":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.teardown_appcontext
    def teardown(exc):
        if exc is not None:
            db.rollback()
        db.close()

    # ------------------------------------------------------------ errors
    @app.errorhandler(404)
    def not_found(_e):
        return render_template("message.html", title="Page not found",
                               text="The page you are looking for does not exist.", tone="warn"), 404

    @app.errorhandler(413)
    def too_large(_e):
        return render_template("message.html", title="File too large", text="Please upload a file under 2 MB.",
                               tone="warn"), 413

    @app.errorhandler(pymysql.MySQLError)
    def database_error(e):
        app.logger.error("Database error: %s", e)
        if request.path.startswith("/api/"):
            return {"ok": False, "error": "The database is not reachable right now. Please try again."}, 503
        return render_template("message.html", title="Database unavailable",
                               text="The server could not reach its database. Please try again in a moment. "
                                    "If you are the administrator, check the DB_* settings.", tone="warn"), 503

    @app.errorhandler(500)
    def server_error(_e):
        if request.path.startswith("/api/"):
            return {"ok": False, "error": "Something went wrong on the server."}, 500
        return render_template("message.html", title="Something went wrong",
                               text="An unexpected error occurred. Please try again.", tone="warn"), 500

    return app


app = create_app()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    app.run(host="0.0.0.0", port=port, debug=not config.IS_PROD)