"""Student pages: dashboard, exam link -> rules -> exam -> submitted."""
import time

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

import config
import db
from routes.common import current_user, login_required
from services import exams

bp = Blueprint("student", __name__)


def message(title, text, tone="info", status=200, **extra):
    return render_template("message.html", title=title, text=text, tone=tone, **extra), status


@bp.get("/")
def home():
    if current_user():
        return redirect(url_for("student.dashboard"))
    return redirect(url_for("auth.login_page"))


@bp.get("/dashboard")
@login_required
def dashboard():
    user = g.user
    cards = []
    for exam in db.query("SELECT * FROM exams WHERE is_active=1 ORDER BY start_at DESC"):
        if not exams.is_eligible(user, exam):
            continue
        att = exams.finalize_if_expired(exams.attempt_for(exam["id"], user["id"]))
        status = exams.exam_status(exam)
        if att and att["submitted_at"]:
            state = "submitted"
        elif att:
            state = "in_progress"
        else:
            state = status
        cards.append({"exam": exam, "state": state, "attempt": att,
                      "questions": db.scalar("SELECT COUNT(*) FROM questions WHERE exam_id=%s", (exam["id"],), 0)})
    return render_template("student/dashboard.html", cards=cards)


def _rules_for(exam):
    custom = [line.strip() for line in (exam["rules"] or "").splitlines() if line.strip()]
    return custom or config.DEFAULT_RULES


def _system_rules(exam):
    grace, allowed = int(exam["grace_seconds"]), int(exam["allowed_warnings"])
    items = []
    if grace == 0:
        leave = "If you leave the exam window, even for a moment (another tab, another app, locking the phone)"
    else:
        leave = (f"If you leave the exam window (another tab, another app, locking the phone) for more than "
                 f"{grace} seconds")
    if allowed == 0:
        items.append(f"{leave}, your exam is submitted automatically.")
    else:
        items.append(f"{leave}, you get a warning. After {allowed} warning{'s' if allowed != 1 else ''}, "
                     f"the next time submits your exam automatically.")
    if grace:
        items.append(f"Short interruptions of up to {grace} seconds, such as a notification pop-up, do not submit "
                     f"your exam, but they are recorded.")
    items.append(f"Your {exam['duration_minutes']}-minute timer starts when you press Begin Exam and keeps running "
                 f"even if you close the page.")
    if exam["shuffle_questions"] or exam["shuffle_options"]:
        items.append("Questions and options appear in a different order for each student.")
    if exam["block_copy"]:
        items.append("Copying, text selection and right-click are disabled and recorded.")
    items.append("Results are not shown to students.")
    return items


def _entry_checks(exam, user):
    """Returns a (response) tuple when the student cannot enter, otherwise None."""
    if not exam["is_active"]:
        return message("Exam link is not active", "This exam link has been disabled by the administrator.", "warn", 403)
    if not exams.is_eligible(user, exam):
        return message("This exam is not for you",
                       f"This exam is for: {exams.audience_text(exam)}. Your profile is {user['branch']} / {user['year']}.",
                       "warn", 403)
    return None


@bp.get("/exam/<slug>")
@login_required
def entry(slug):
    exam = exams.exam_by_slug(slug) or abort(404)
    blocked = _entry_checks(exam, g.user)
    if blocked:
        return blocked
    att = exams.finalize_if_expired(exams.attempt_for(exam["id"], g.user["id"]))
    if att:
        if att["submitted_at"]:
            return message("Exam already submitted", "You have already completed this exam. "
                           "Results are not shown to students.", "info")
        return redirect(url_for("student.attempt_page", attempt_id=att["id"]))
    status = exams.exam_status(exam)
    if status == "upcoming":
        return message("This exam has not started yet", "It opens on:", "info", ts=exam["start_at"], exam=exam)
    if status == "closed":
        return message("This exam is closed", "The exam window ended on:", "warn", ts=exam["end_at"], exam=exam)
    session[f"rules_{exam['id']}"] = int(time.time())
    return render_template(
        "student/rules.html", exam=exam, rules=_rules_for(exam), system_rules=_system_rules(exam),
        questions=db.scalar("SELECT COUNT(*) FROM questions WHERE exam_id=%s", (exam["id"],), 0),
        marks=exams.max_marks(exam["id"]))


@bp.post("/exam/<slug>/begin")
@login_required
def begin(slug):
    exam = exams.exam_by_slug(slug) or abort(404)
    blocked = _entry_checks(exam, g.user)
    if blocked:
        return blocked
    existing = exams.attempt_for(exam["id"], g.user["id"])
    if existing:
        return redirect(url_for("student.attempt_page", attempt_id=existing["id"]))
    if exams.exam_status(exam) != "live":
        flash("This exam is not open right now.", "error")
        return redirect(url_for("student.entry", slug=slug))
    if not request.form.get("agree"):
        flash("Please confirm that you have read and accept the rules.", "error")
        return redirect(url_for("student.entry", slug=slug))
    seen = session.get(f"rules_{exam['id']}", 0)
    if time.time() - seen < int(exam["min_read_seconds"]) - 1:
        flash("Please take the time to read the rules before you begin.", "error")
        return redirect(url_for("student.entry", slug=slug))
    try:
        attempt_id = exams.create_attempt(exam, g.user)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("student.dashboard"))
    return redirect(url_for("student.attempt_page", attempt_id=attempt_id))


def _own_attempt(attempt_id):
    att = exams.load_attempt(attempt_id)
    if att is None or att["user_id"] != g.user["id"]:
        abort(404)
    return exams.finalize_if_expired(att)


@bp.get("/attempt/<int:attempt_id>")
@login_required
def attempt_page(attempt_id):
    att = _own_attempt(attempt_id)
    if att["submitted_at"]:
        return redirect(url_for("student.done", attempt_id=attempt_id))
    exam = exams.exam_by_id(att["exam_id"])
    return render_template("student/exam.html", exam=exam, attempt=att)


@bp.get("/attempt/<int:attempt_id>/done")
@login_required
def done(attempt_id):
    att = _own_attempt(attempt_id)
    if not att["submitted_at"]:
        return redirect(url_for("student.attempt_page", attempt_id=attempt_id))
    exam = exams.exam_by_id(att["exam_id"])
    return render_template("student/done.html", exam=exam, reason=att["end_reason"])
