"""JSON API used by the exam page (timer, autosave, window-switch reports, submit)."""
from flask import Blueprint, g, url_for

from flask import request

import config
from routes.common import api_login_required, jerr
from services import exams

bp = Blueprint("exam_api", __name__, url_prefix="/api/attempt")

EVENT_MAP = {"copy": "COPY", "cut": "CUT", "contextmenu": "CONTEXT_MENU", "devtools": "DEVTOOLS_KEY"}


def _load(attempt_id):
    """Return (attempt, exam) for the logged-in student, auto-submitting if time has run out."""
    att = exams.load_attempt(attempt_id)
    if att is None or att["user_id"] != g.user["id"]:
        return None, None
    att = exams.finalize_if_expired(att)
    return att, exams.exam_by_id(att["exam_id"])


def _status(att, exam, **extra):
    done = att["submitted_at"] is not None
    payload = {"ok": True, "status": "submitted" if done else "in_progress", "server_now": exams.now(),
               "deadline": att["deadline_at"], "warnings": exams.warnings_info(att, exam), **extra}
    if done:
        payload["reason"] = att["end_reason"]
        payload["redirect"] = url_for("student.done", attempt_id=att["id"])
    return payload


@bp.get("/<int:attempt_id>/state")
@api_login_required
def state(attempt_id):
    att, exam = _load(attempt_id)
    if att is None:
        return jerr("Attempt not found.", 404)
    if att["submitted_at"]:
        return _status(att, exam)
    questions, answers = exams.student_view(att)
    return _status(att, exam, questions=questions, answers=answers,
                   cfg={"title": exam["title"], "grace_seconds": exam["grace_seconds"],
                         "block_copy": bool(exam["block_copy"]), "allowed_warnings": exam["allowed_warnings"]})


@bp.post("/<int:attempt_id>/answer")
@api_login_required
def answer(attempt_id):
    att, exam = _load(attempt_id)
    if att is None:
        return jerr("Attempt not found.", 404)
    if att["submitted_at"]:
        return _status(att, exam)
    data = request.get_json(silent=True) or {}
    try:
        exams.save_answer(attempt_id, int(data.get("qid")), data.get("choice"))
    except (ValueError, TypeError) as e:
        return jerr(str(e) or "Invalid answer.")
    return {"ok": True, "status": "in_progress"}


@bp.post("/<int:attempt_id>/away")
@api_login_required
def away(attempt_id):
    att, exam = _load(attempt_id)
    if att is None:
        return jerr("Attempt not found.", 404)
    if att["submitted_at"]:
        return _status(att, exam)
    action = (request.get_json(silent=True) or {}).get("action")
    if action not in ("start", "check", "end"):
        return jerr("Invalid action.")
    result = exams.handle_away(attempt_id, exam, action)
    att = exams.load_attempt(attempt_id)
    return _status(att, exam, violation=result["violation"])


@bp.post("/<int:attempt_id>/event")
@api_login_required
def event(attempt_id):
    att, exam = _load(attempt_id)
    if att is None:
        return jerr("Attempt not found.", 404)
    if att["submitted_at"]:
        return _status(att, exam)
    etype = EVENT_MAP.get((request.get_json(silent=True) or {}).get("type"))
    if etype is None:
        return jerr("Invalid event.")
    exams.record_event(attempt_id, etype, "")
    return {"ok": True}


@bp.post("/<int:attempt_id>/ping")
@api_login_required
def ping(attempt_id):
    att, exam = _load(attempt_id)
    if att is None:
        return jerr("Attempt not found.", 404)
    return _status(att, exam)


@bp.post("/<int:attempt_id>/submit")
@api_login_required
def submit(attempt_id):
    att, exam = _load(attempt_id)
    if att is None:
        return jerr("Attempt not found.", 404)
    if not att["submitted_at"]:
        exams.finalize(attempt_id, "SUBMITTED")
        att = exams.load_attempt(attempt_id)
    return _status(att, exam)
