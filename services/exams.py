"""Exam engine: eligibility, attempts, shuffling, autosave, window-switch handling, scoring, risk."""
import json
import random
import re
import secrets
import time

import config
import db


def now():
    return int(time.time())


def now_ms():
    return int(time.time() * 1000)


def jload(raw, default):
    try:
        return json.loads(raw) if raw else default
    except ValueError:
        return default


# ------------------------------------------------------------------ exams
def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:30].strip("-") or "exam"


def new_slug(title):
    for _ in range(20):
        slug = f"{slugify(title)}-{secrets.token_hex(3)}"
        if db.scalar("SELECT COUNT(*) FROM exams WHERE slug=%s", (slug,)) == 0:
            return slug
    return f"exam-{secrets.token_hex(8)}"


def exam_by_slug(slug):
    return db.one("SELECT * FROM exams WHERE slug=%s", (slug,))


def exam_by_id(exam_id):
    return db.one("SELECT * FROM exams WHERE id=%s", (exam_id,))


def exam_status(exam, t=None):
    t = t or now()
    if not exam["is_active"]:
        return "disabled"
    if t < exam["start_at"]:
        return "upcoming"
    if t > exam["end_at"]:
        return "closed"
    return "live"


def audience(exam):
    """(branches, years) lists; an empty list means 'everyone'."""
    return jload(exam["branches"], []), jload(exam["years"], [])


def is_eligible(user, exam):
    branches, years = audience(exam)
    return (not branches or user["branch"] in branches) and (not years or user["year"] in years)


def audience_text(exam):
    branches, years = audience(exam)
    return f"{', '.join(branches) if branches else 'All branches'} / {', '.join(years) if years else 'All years'}"


def questions_of(exam_id):
    rows = db.query("SELECT * FROM questions WHERE exam_id=%s ORDER BY position, id", (exam_id,))
    for r in rows:
        r["options"] = jload(r["options"], [])
        r["marks"] = float(r["marks"])
    return rows


def max_marks(exam_id):
    return float(db.scalar("SELECT COALESCE(SUM(marks), 0) FROM questions WHERE exam_id=%s", (exam_id,), 0))


# ------------------------------------------------------------------ attempts
def load_attempt(attempt_id):
    att = db.one("SELECT * FROM attempts WHERE id=%s", (attempt_id,))
    return _parse_attempt(att)


def attempt_for(exam_id, user_id):
    return _parse_attempt(db.one("SELECT * FROM attempts WHERE exam_id=%s AND user_id=%s", (exam_id, user_id)))


def _parse_attempt(att):
    if att is None:
        return None
    att["question_order"] = jload(att["question_order"], [])
    att["option_order"] = jload(att["option_order"], {})
    att["answers"] = jload(att["answers"], {})
    att["score"] = float(att["score"])
    att["max_score"] = float(att["max_score"])
    return att


def create_attempt(exam, user):
    """Start an attempt: the timer starts NOW. Raises ValueError if it cannot be created."""
    qs = questions_of(exam["id"])
    if not qs:
        raise ValueError("This exam has no questions yet.")
    order = [q["id"] for q in qs]
    if exam["shuffle_questions"]:
        random.shuffle(order)
    option_order = {}
    for q in qs:
        idx = list(range(len(q["options"])))
        if exam["shuffle_options"]:
            random.shuffle(idx)
        option_order[str(q["id"])] = idx
    t = now()
    try:
        attempt_id = db.insert(
            "INSERT INTO attempts(exam_id, user_id, started_at, deadline_at, max_score, question_order, option_order, answers) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (exam["id"], user["id"], t, t + int(exam["duration_minutes"]) * 60, sum(q["marks"] for q in qs),
             json.dumps(order), json.dumps(option_order), "{}"))
    except db.IntegrityError:
        raise ValueError("You have already started this exam.")
    record_event(attempt_id, "SESSION_START", "Exam started")
    return attempt_id


def record_event(attempt_id, etype, detail=""):
    points = config.RISK_POINTS.get(etype, 0)
    db.insert("INSERT INTO events(attempt_id, ts, etype, detail, points) VALUES (%s,%s,%s,%s,%s)",
              (attempt_id, now(), etype, (detail or "")[:250], points))
    if points:
        db.update("UPDATE attempts SET risk_points = risk_points + %s WHERE id=%s", (points, attempt_id))


def verdict_for(points):
    for limit, label in config.VERDICTS:
        if points < limit:
            return label
    return config.VERDICTS[-1][1]


def grade(att):
    qmap = {q["id"]: q for q in questions_of(att["exam_id"])}
    score = 0.0
    for qid in att["question_order"]:
        q = qmap.get(qid)
        if q is not None and att["answers"].get(str(qid)) == q["correct_index"]:
            score += q["marks"]
    return score


def finalize(attempt_id, reason, at=None):
    """Submit an attempt exactly once. Returns True if this call performed the submission."""
    t = at or now()
    changed = db.update("UPDATE attempts SET submitted_at=%s, end_reason=%s, away_since=NULL "
                        "WHERE id=%s AND submitted_at IS NULL", (t, reason, attempt_id))
    if not changed:
        return False
    att = load_attempt(attempt_id)
    record_event(attempt_id, "SESSION_END", config.END_REASONS.get(reason, reason))
    points = int(db.scalar("SELECT COALESCE(SUM(points), 0) FROM events WHERE attempt_id=%s", (attempt_id,), 0))
    db.update("UPDATE attempts SET score=%s, risk_points=%s, verdict=%s WHERE id=%s",
              (grade(att), points, verdict_for(points), attempt_id))
    return True


def finalize_if_expired(att):
    """Lazily auto-submit an attempt whose time is over (no background job is needed)."""
    if att and att["submitted_at"] is None and now() > att["deadline_at"] + 2:
        finalize(att["id"], "TIME_UP", at=att["deadline_at"])
        return load_attempt(att["id"])
    return att


def finalize_expired(exam_id=None):
    sql = "SELECT id FROM attempts WHERE submitted_at IS NULL AND deadline_at < %s"
    args = [now() - 2]
    if exam_id:
        sql += " AND exam_id=%s"
        args.append(exam_id)
    for row in db.query(sql, tuple(args)):
        att = load_attempt(row["id"])
        finalize(att["id"], "TIME_UP", at=att["deadline_at"])


# ------------------------------------------------------------------ the exam as the student sees it
def student_view(att):
    """Questions in this student's shuffled order (without correct answers) + saved answers (display index)."""
    qmap = {q["id"]: q for q in questions_of(att["exam_id"])}
    questions, answers = [], {}
    for qid in att["question_order"]:
        q = qmap.get(qid)
        if q is None:
            continue
        order = att["option_order"].get(str(qid))
        if not order or sorted(order) != list(range(len(q["options"]))):
            order = list(range(len(q["options"])))          # question edited after start: fall back safely
        questions.append({"id": qid, "text": q["text"], "marks": q["marks"],
                          "options": [q["options"][i] for i in order]})
        saved = att["answers"].get(str(qid))
        if saved is not None and saved in order:
            answers[str(qid)] = order.index(saved)
    return questions, answers


def save_answer(attempt_id, qid, choice):
    """choice = index in the shuffled list shown to the student, or None to clear."""
    row = db.one("SELECT * FROM attempts WHERE id=%s FOR UPDATE", (attempt_id,))
    att = _parse_attempt(row)
    if att is None or att["submitted_at"] is not None:
        return False
    if qid not in att["question_order"]:
        raise ValueError("Unknown question.")
    order = att["option_order"].get(str(qid)) or []
    answers = att["answers"]
    if choice is None:
        answers.pop(str(qid), None)
    else:
        if not isinstance(choice, int) or not 0 <= choice < len(order):
            raise ValueError("Invalid option.")
        answers[str(qid)] = order[choice]
    db.update("UPDATE attempts SET answers=%s WHERE id=%s", (json.dumps(answers), attempt_id))
    return True


# ------------------------------------------------------------------ leaving the exam window
def handle_away(attempt_id, exam, action):
    """Process a 'start' / 'check' / 'end' report about the student leaving the exam window.

    Leaving for LONGER than the grace time is a violation. Coming back within the grace time
    (for example after a notification pop-up) is only logged. When violations exceed the number
    of allowed warnings the exam is submitted automatically.
    """
    out = {"violation": False, "submitted": False}
    row = db.one("SELECT away_since, violations, submitted_at FROM attempts WHERE id=%s FOR UPDATE", (attempt_id,))
    if row is None or row["submitted_at"] is not None:
        out["submitted"] = row is not None
        return out
    t = now_ms()
    grace_ms = int(exam["grace_seconds"]) * 1000
    away = row["away_since"]

    if action == "start":
        if away is None:
            db.update("UPDATE attempts SET away_since=%s WHERE id=%s", (t, attempt_id))
        return out
    if away is None:
        return out

    duration = t - away
    if action == "check":
        if duration + 200 < grace_ms:            # the client timer fired too early; ignore
            return out
        is_long = True
        detail = f"Away for more than {exam['grace_seconds']}s"
    else:                                         # "end"
        is_long = duration > grace_ms or grace_ms == 0
        detail = f"Away for {duration / 1000:.1f}s (grace time {exam['grace_seconds']}s)"

    db.update("UPDATE attempts SET away_since=NULL WHERE id=%s", (attempt_id,))
    if not is_long:
        record_event(attempt_id, "AWAY_SHORT", detail)
        return out

    violations = row["violations"] + 1
    db.update("UPDATE attempts SET violations=%s WHERE id=%s", (violations, attempt_id))
    record_event(attempt_id, "AWAY_LONG", detail)
    out["violation"] = True
    if violations > int(exam["allowed_warnings"]):
        finalize(attempt_id, "WINDOW_SWITCH")
        out["submitted"] = True
    return out


def warnings_info(att, exam):
    return {"used": att["violations"], "allowed": int(exam["allowed_warnings"])}
