"""Admin panel: dashboard, exams (+links), questions, students, results, settings."""
import calendar
import csv
import io
import json
import time
from datetime import datetime

from flask import (Blueprint, Response, abort, flash, g, redirect, render_template, request, session,
                   url_for)

import config
import db
import mailer
import schema
import security
from routes.common import admin_required, current_admin, safe_next
from services import exams, similarity

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _lists():
    return (schema.get_setting("branches", config.DEFAULT_BRANCHES),
            schema.get_setting("years", config.DEFAULT_YEARS))


def _int(value, lo, hi, label):
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ValueError(f"{label} must be a whole number.")
    if not lo <= number <= hi:
        raise ValueError(f"{label} must be between {lo} and {hi}.")
    return number


# ------------------------------------------------------------------ login
@bp.get("/login")
def login_page():
    if current_admin():
        return redirect(url_for("admin.dashboard"))
    return render_template("admin/login.html")


@bp.post("/login")
def login_submit():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password", "")

    def fail(message):
        flash(message, "error")
        return render_template("admin/login.html", username=username), 401

    if security.rate_limited(f"admin:ip:{security.client_ip()}", 15, 600):
        return fail("Too many attempts. Please wait a few minutes.")
    admin = db.one("SELECT * FROM admins WHERE username=%s", (username,))
    now = int(time.time())
    if admin and admin["locked_until"] > now:
        return fail("This account is temporarily locked. Please try again later.")
    if not admin or not security.verify_password(admin["password_hash"], password):
        if admin:
            fails = admin["failed_logins"] + 1
            locked = now + config.LOGIN_LOCK_SECONDS if fails >= config.LOGIN_MAX_FAILS else 0
            db.update("UPDATE admins SET failed_logins=%s, locked_until=%s WHERE id=%s",
                      (0 if locked else fails, locked, admin["id"]))
        return fail("Incorrect username or password.")
    db.update("UPDATE admins SET failed_logins=0, locked_until=0 WHERE id=%s", (admin["id"],))
    session.clear()
    session["admin_id"] = admin["id"]
    session.permanent = True
    return redirect(url_for("admin.dashboard"))


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin.login_page"))


# ------------------------------------------------------------------ dashboard
@bp.get("/")
@admin_required
def dashboard():
    exams.finalize_expired()
    t = int(time.time())
    stats = {
        "students": db.scalar("SELECT COUNT(*) FROM users", (), 0),
        "exams": db.scalar("SELECT COUNT(*) FROM exams", (), 0),
        "live": db.scalar("SELECT COUNT(*) FROM exams WHERE is_active=1 AND start_at<=%s AND end_at>=%s", (t, t), 0),
        "in_progress": db.scalar("SELECT COUNT(*) FROM attempts WHERE submitted_at IS NULL", (), 0),
        "submitted": db.scalar("SELECT COUNT(*) FROM attempts WHERE submitted_at IS NOT NULL", (), 0),
        "flagged": db.scalar("SELECT COUNT(*) FROM attempts WHERE submitted_at IS NOT NULL AND verdict<>'CLEAN'", (), 0),
    }
    verdicts = {"CLEAN": 0, "SUSPICIOUS": 0, "HIGH RISK": 0}
    for row in db.query("SELECT verdict, COUNT(*) AS n FROM attempts WHERE submitted_at IS NOT NULL GROUP BY verdict"):
        if row["verdict"] in verdicts:
            verdicts[row["verdict"]] = row["n"]
    per_exam = db.query(
        "SELECT e.title, COUNT(a.id) AS n FROM exams e LEFT JOIN attempts a ON a.exam_id=e.id "
        "GROUP BY e.id, e.title ORDER BY e.id DESC LIMIT 8")
    recent = db.query(
        "SELECT a.id, a.submitted_at, a.verdict, a.score, a.max_score, u.name, u.roll, e.title "
        "FROM attempts a JOIN users u ON u.id=a.user_id JOIN exams e ON e.id=a.exam_id "
        "WHERE a.submitted_at IS NOT NULL ORDER BY a.submitted_at DESC LIMIT 8")
    return render_template("admin/dashboard.html", stats=stats, verdicts=verdicts, per_exam=per_exam, recent=recent)


# ------------------------------------------------------------------ exams
@bp.get("/exams")
@admin_required
def exams_list():
    rows = db.query(
        "SELECT e.*, (SELECT COUNT(*) FROM questions q WHERE q.exam_id=e.id) AS qcount, "
        "(SELECT COUNT(*) FROM attempts a WHERE a.exam_id=e.id) AS acount FROM exams e ORDER BY e.id DESC")
    for r in rows:
        r["status"] = exams.exam_status(r)
        r["audience"] = exams.audience_text(r)
        r["link"] = url_for("student.entry", slug=r["slug"], _external=True)
    return render_template("admin/exams.html", exams=rows)


def _read_exam_form(form):
    """Validate the exam form. Returns (values dict, error message or None)."""
    branches, years = _lists()
    values = {
        "title": (form.get("title") or "").strip(),
        "description": (form.get("description") or "").strip(),
        "rules": (form.get("rules") or "").strip(),
        "shuffle_questions": 1 if form.get("shuffle_questions") else 0,
        "shuffle_options": 1 if form.get("shuffle_options") else 0,
        "block_copy": 1 if form.get("block_copy") else 0,
        "is_active": 1 if form.get("is_active") else 0,
        "branches": json.dumps([b for b in form.getlist("branches") if b in branches]),
        "years": json.dumps([y for y in form.getlist("years") if y in years]),
    }
    try:
        if not values["title"]:
            raise ValueError("Please enter an exam title.")
        values["duration_minutes"] = _int(form.get("duration_minutes"), 1, 600, "Duration")
        values["min_read_seconds"] = _int(form.get("min_read_seconds") or 0, 0, 600, "Minimum reading time")
        values["grace_seconds"] = _int(form.get("grace_seconds") or 0, 0, 120, "Grace time")
        values["allowed_warnings"] = _int(form.get("allowed_warnings") or 0, 0, 10, "Allowed warnings")
        offset = _int(form.get("tz_offset") or 0, -900, 900, "Time zone")
        values["start_at"] = _parse_local(form.get("start_at"), offset, "Start time")
        values["end_at"] = _parse_local(form.get("end_at"), offset, "End time")
        if values["end_at"] <= values["start_at"]:
            raise ValueError("The end time must be after the start time.")
    except ValueError as e:
        return values, str(e)
    return values, None


def _parse_local(raw, offset_minutes, label):
    """'2026-11-05T10:30' typed in the admin's browser -> UTC epoch (offset = JS getTimezoneOffset())."""
    try:
        dt = datetime.strptime((raw or "").strip(), "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ValueError(f"Please choose a valid {label.lower()}.")
    return calendar.timegm(dt.timetuple()) + offset_minutes * 60


def _exam_form_context(exam=None, values=None, error=None):
    branches, years = _lists()
    return dict(exam=exam, v=values or {}, error=error, branch_options=branches, year_options=years,
                sel_branches=exams.jload((values or exam or {}).get("branches"), []) if (values or exam) else [],
                sel_years=exams.jload((values or exam or {}).get("years"), []) if (values or exam) else [])


@bp.route("/exams/new", methods=["GET", "POST"])
@admin_required
def exam_new():
    if request.method == "GET":
        return render_template("admin/exam_form.html", **_exam_form_context(
            values={"duration_minutes": 30, "min_read_seconds": 15, "grace_seconds": 5, "allowed_warnings": 0,
                    "shuffle_questions": 1, "shuffle_options": 1, "block_copy": 1, "is_active": 1,
                    "branches": "[]", "years": "[]"}))
    values, error = _read_exam_form(request.form)
    if error:
        values.update({k: request.form.get(k, "") for k in ("duration_minutes", "min_read_seconds", "grace_seconds",
                                                             "allowed_warnings", "start_at", "end_at")})
        return render_template("admin/exam_form.html", **_exam_form_context(values=values, error=error)), 400
    exam_id = db.insert(
        "INSERT INTO exams(slug, title, description, duration_minutes, start_at, end_at, rules, min_read_seconds, "
        "shuffle_questions, shuffle_options, grace_seconds, allowed_warnings, block_copy, branches, years, is_active, "
        "created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (exams.new_slug(values["title"]), values["title"], values["description"], values["duration_minutes"],
         values["start_at"], values["end_at"], values["rules"], values["min_read_seconds"],
         values["shuffle_questions"], values["shuffle_options"], values["grace_seconds"], values["allowed_warnings"],
         values["block_copy"], values["branches"], values["years"], values["is_active"], int(time.time())))
    flash("Exam created. Now add its questions.", "ok")
    return redirect(url_for("admin.questions", exam_id=exam_id))


@bp.route("/exams/<int:exam_id>/edit", methods=["GET", "POST"])
@admin_required
def exam_edit(exam_id):
    exam = exams.exam_by_id(exam_id) or abort(404)
    if request.method == "GET":
        return render_template("admin/exam_form.html", **_exam_form_context(exam=exam, values=dict(exam)))
    values, error = _read_exam_form(request.form)
    if error:
        values.update({k: request.form.get(k, "") for k in ("duration_minutes", "min_read_seconds", "grace_seconds",
                                                             "allowed_warnings", "start_at", "end_at")})
        return render_template("admin/exam_form.html", **_exam_form_context(exam=exam, values=values, error=error)), 400
    db.update(
        "UPDATE exams SET title=%s, description=%s, duration_minutes=%s, start_at=%s, end_at=%s, rules=%s, "
        "min_read_seconds=%s, shuffle_questions=%s, shuffle_options=%s, grace_seconds=%s, allowed_warnings=%s, "
        "block_copy=%s, branches=%s, years=%s, is_active=%s WHERE id=%s",
        (values["title"], values["description"], values["duration_minutes"], values["start_at"], values["end_at"],
         values["rules"], values["min_read_seconds"], values["shuffle_questions"], values["shuffle_options"],
         values["grace_seconds"], values["allowed_warnings"], values["block_copy"], values["branches"],
         values["years"], values["is_active"], exam_id))
    flash("Exam updated.", "ok")
    return redirect(url_for("admin.exams_list"))


@bp.post("/exams/<int:exam_id>/toggle")
@admin_required
def exam_toggle(exam_id):
    exam = exams.exam_by_id(exam_id) or abort(404)
    db.update("UPDATE exams SET is_active=%s WHERE id=%s", (0 if exam["is_active"] else 1, exam_id))
    flash("Link disabled." if exam["is_active"] else "Link enabled.", "ok")
    return redirect(url_for("admin.exams_list"))


@bp.post("/exams/<int:exam_id>/regenerate-link")
@admin_required
def exam_regenerate(exam_id):
    exam = exams.exam_by_id(exam_id) or abort(404)
    db.update("UPDATE exams SET slug=%s WHERE id=%s", (exams.new_slug(exam["title"]), exam_id))
    flash("A new link was generated. The old link no longer works.", "ok")
    return redirect(url_for("admin.exams_list"))


@bp.post("/exams/<int:exam_id>/duplicate")
@admin_required
def exam_duplicate(exam_id):
    exam = exams.exam_by_id(exam_id) or abort(404)
    new_id = db.insert(
        "INSERT INTO exams(slug, title, description, duration_minutes, start_at, end_at, rules, min_read_seconds, "
        "shuffle_questions, shuffle_options, grace_seconds, allowed_warnings, block_copy, branches, years, is_active, "
        "created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (exams.new_slug(exam["title"]), f"{exam['title']} (copy)", exam["description"], exam["duration_minutes"],
         exam["start_at"], exam["end_at"], exam["rules"], exam["min_read_seconds"], exam["shuffle_questions"],
         exam["shuffle_options"], exam["grace_seconds"], exam["allowed_warnings"], exam["block_copy"],
         exam["branches"], exam["years"], 0, int(time.time())))
    db.update("INSERT INTO questions(exam_id, position, text, options, correct_index, marks) "
              "SELECT %s, position, text, options, correct_index, marks FROM questions WHERE exam_id=%s",
              (new_id, exam_id))
    flash("Exam duplicated. The copy is disabled until you enable its link.", "ok")
    return redirect(url_for("admin.exam_edit", exam_id=new_id))


@bp.post("/exams/<int:exam_id>/delete")
@admin_required
def exam_delete(exam_id):
    db.update("DELETE FROM exams WHERE id=%s", (exam_id,))
    flash("Exam and all its attempts were deleted.", "ok")
    return redirect(url_for("admin.exams_list"))


# ------------------------------------------------------------------ questions
@bp.get("/exams/<int:exam_id>/questions")
@admin_required
def questions(exam_id):
    exam = exams.exam_by_id(exam_id) or abort(404)
    return render_template("admin/questions.html", exam=exam, questions=exams.questions_of(exam_id),
                           attempts=db.scalar("SELECT COUNT(*) FROM attempts WHERE exam_id=%s", (exam_id,), 0))


def _read_question_form(form):
    text = (form.get("text") or "").strip()
    if not text:
        raise ValueError("Please enter the question text.")
    slots = [(i, (form.get(f"opt{i}") or "").strip()) for i in range(6)]
    filled = [(i, o) for i, o in slots if o]
    if len(filled) < 2:
        raise ValueError("Please enter at least two options.")
    try:
        correct_slot = int(form.get("correct", ""))
    except ValueError:
        raise ValueError("Please select the correct option.")
    positions = [i for i, _ in filled]
    if correct_slot not in positions:
        raise ValueError("The correct option must be one of the options you filled in.")
    try:
        marks = float(form.get("marks") or 1)
    except ValueError:
        raise ValueError("Marks must be a number.")
    if not 0 < marks <= 100:
        raise ValueError("Marks must be between 0 and 100.")
    return text, [o for _, o in filled], positions.index(correct_slot), marks


@bp.route("/exams/<int:exam_id>/questions/new", methods=["GET", "POST"])
@admin_required
def question_new(exam_id):
    exam = exams.exam_by_id(exam_id) or abort(404)
    if request.method == "GET":
        return render_template("admin/question_form.html", exam=exam, q=None, form={})
    try:
        text, options, correct, marks = _read_question_form(request.form)
    except ValueError as e:
        flash(str(e), "error")
        return render_template("admin/question_form.html", exam=exam, q=None, form=request.form), 400
    pos = int(db.scalar("SELECT COALESCE(MAX(position), 0) + 1 FROM questions WHERE exam_id=%s", (exam_id,), 1))
    db.insert("INSERT INTO questions(exam_id, position, text, options, correct_index, marks) VALUES (%s,%s,%s,%s,%s,%s)",
              (exam_id, pos, text, json.dumps(options, ensure_ascii=False), correct, marks))
    flash("Question added.", "ok")
    if request.form.get("another"):
        return redirect(url_for("admin.question_new", exam_id=exam_id))
    return redirect(url_for("admin.questions", exam_id=exam_id))


@bp.route("/questions/<int:qid>/edit", methods=["GET", "POST"])
@admin_required
def question_edit(qid):
    row = db.one("SELECT * FROM questions WHERE id=%s", (qid,)) or abort(404)
    exam = exams.exam_by_id(row["exam_id"])
    q = {"id": qid, "text": row["text"], "options": exams.jload(row["options"], []), "correct": row["correct_index"],
         "marks": float(row["marks"])}
    if request.method == "GET":
        return render_template("admin/question_form.html", exam=exam, q=q, form={})
    try:
        text, options, correct, marks = _read_question_form(request.form)
    except ValueError as e:
        flash(str(e), "error")
        return render_template("admin/question_form.html", exam=exam, q=q, form=request.form), 400
    db.update("UPDATE questions SET text=%s, options=%s, correct_index=%s, marks=%s WHERE id=%s",
              (text, json.dumps(options, ensure_ascii=False), correct, marks, qid))
    flash("Question updated.", "ok")
    return redirect(url_for("admin.questions", exam_id=row["exam_id"]))


@bp.post("/questions/<int:qid>/delete")
@admin_required
def question_delete(qid):
    row = db.one("SELECT exam_id FROM questions WHERE id=%s", (qid,)) or abort(404)
    db.update("DELETE FROM questions WHERE id=%s", (qid,))
    flash("Question deleted.", "ok")
    return redirect(url_for("admin.questions", exam_id=row["exam_id"]))


@bp.post("/questions/<int:qid>/move")
@admin_required
def question_move(qid):
    row = db.one("SELECT exam_id FROM questions WHERE id=%s", (qid,)) or abort(404)
    direction = -1 if request.form.get("dir") == "up" else 1
    ids = [q["id"] for q in exams.questions_of(row["exam_id"])]
    i = ids.index(qid)
    j = i + direction
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
        for pos, _id in enumerate(ids, 1):
            db.update("UPDATE questions SET position=%s WHERE id=%s", (pos, _id))
    return redirect(url_for("admin.questions", exam_id=row["exam_id"]))


TEMPLATE_HEADER = ["question", "option_a", "option_b", "option_c", "option_d", "option_e", "option_f", "correct", "marks"]


@bp.get("/questions-template.csv")
@admin_required
def questions_template():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(TEMPLATE_HEADER)
    w.writerow(["Which protocol secures web traffic?", "HTTP", "HTTPS", "FTP", "Telnet", "", "", "B", "1"])
    w.writerow(["What does a firewall do?", "Speeds up internet", "Stores passwords", "Filters network traffic",
                "Removes viruses", "", "", "C", "2"])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=questions_template.csv"})


def _read_import_rows(file):
    name = (file.filename or "").lower()
    data = file.read()
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook
        sheet = load_workbook(io.BytesIO(data), read_only=True, data_only=True).active
        return [["" if c is None else str(c).strip() for c in row] for row in sheet.iter_rows(values_only=True)]
    if name.endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text))]
    raise ValueError("Please upload a .csv or .xlsx file.")


@bp.post("/exams/<int:exam_id>/import")
@admin_required
def questions_import(exam_id):
    exams.exam_by_id(exam_id) or abort(404)
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Please choose a file to import.", "error")
        return redirect(url_for("admin.questions", exam_id=exam_id))
    try:
        rows = [r for r in _read_import_rows(file) if any(r)]
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.questions", exam_id=exam_id))
    except Exception:
        flash("That file could not be read. Please use the template.", "error")
        return redirect(url_for("admin.questions", exam_id=exam_id))
    if rows and rows[0] and rows[0][0].lower().startswith("question"):
        rows = rows[1:]
    pos = int(db.scalar("SELECT COALESCE(MAX(position), 0) FROM questions WHERE exam_id=%s", (exam_id,), 0))
    added, errors = 0, []
    for n, row in enumerate(rows, 2):
        row = (row + [""] * 9)[:9]
        text, opts, correct_raw, marks_raw = row[0], row[1:7], row[7].strip().upper(), row[8].strip()
        filled = [(i, o) for i, o in enumerate(opts) if o]
        try:
            if not text:
                raise ValueError("question text is empty")
            if len(filled) < 2:
                raise ValueError("needs at least two options")
            slot = "ABCDEF".index(correct_raw) if correct_raw in list("ABCDEF") else int(correct_raw) - 1
            positions = [i for i, _ in filled]
            if slot not in positions:
                raise ValueError("'correct' must point to a filled option")
            marks = float(marks_raw) if marks_raw else 1.0
            if not 0 < marks <= 100:
                raise ValueError("marks must be between 0 and 100")
        except (ValueError, IndexError) as e:
            errors.append(f"Row {n}: {e}")
            continue
        pos += 1
        db.insert("INSERT INTO questions(exam_id, position, text, options, correct_index, marks) VALUES (%s,%s,%s,%s,%s,%s)",
                  (exam_id, pos, text, json.dumps([o for _, o in filled], ensure_ascii=False), positions.index(slot), marks))
        added += 1
    if added:
        flash(f"Imported {added} question(s).", "ok")
    if errors:
        flash("Skipped " + str(len(errors)) + " row(s): " + "; ".join(errors[:5]) + (" ..." if len(errors) > 5 else ""), "error")
    if not added and not errors:
        flash("The file had no questions.", "error")
    return redirect(url_for("admin.questions", exam_id=exam_id))


# ------------------------------------------------------------------ students
@bp.get("/students")
@admin_required
def students():
    branch, year, q = request.args.get("branch", ""), request.args.get("year", ""), (request.args.get("q") or "").strip()
    sql = ("SELECT u.*, (SELECT COUNT(*) FROM attempts a WHERE a.user_id=u.id AND a.submitted_at IS NOT NULL) AS attempts "
           "FROM users u WHERE 1=1")
    args = []
    if branch:
        sql += " AND u.branch=%s"
        args.append(branch)
    if year:
        sql += " AND u.year=%s"
        args.append(year)
    if q:
        sql += " AND (u.name LIKE %s OR u.roll LIKE %s OR u.email LIKE %s)"
        args += [f"%{q}%"] * 3
    rows = db.query(sql + " ORDER BY LENGTH(u.roll), u.roll LIMIT 2000", tuple(args))
    branches, years = _lists()
    return render_template("admin/students.html", students=rows, branches=branches, years=years,
                           f_branch=branch, f_year=year, f_q=q)


@bp.route("/students/<int:uid>/edit", methods=["GET", "POST"])
@admin_required
def student_edit(uid):
    user = db.one("SELECT * FROM users WHERE id=%s", (uid,)) or abort(404)
    branches, years = _lists()
    if request.method == "GET":
        return render_template("admin/student_edit.html", user=user, branches=branches, years=years)
    name = security.clean_name(request.form.get("name"))
    roll = security.normalize_roll(request.form.get("roll"))
    branch, year = request.form.get("branch", ""), request.form.get("year", "")
    new_password = request.form.get("new_password", "")
    error = None
    if len(name) < 3:
        error = "Please enter the student's full name."
    elif not security.is_valid_roll(roll):
        error = "Please enter a valid Roll No."
    elif not branch or not year:
        error = "Please choose a branch and year."
    elif new_password and security.password_problem(new_password):
        error = security.password_problem(new_password)
    if error:
        flash(error, "error")
        return render_template("admin/student_edit.html", user=user, branches=branches, years=years), 400
    try:
        db.update("UPDATE users SET name=%s, roll=%s, branch=%s, year=%s WHERE id=%s", (name, roll, branch, year, uid))
    except db.IntegrityError:
        flash("Another student already has that Roll No.", "error")
        return render_template("admin/student_edit.html", user=user, branches=branches, years=years), 409
    if new_password:
        db.update("UPDATE users SET password_hash=%s, session_token=NULL, failed_logins=0, locked_until=0 WHERE id=%s",
                  (security.hash_password(new_password), uid))
    flash("Student updated.", "ok")
    return redirect(url_for("admin.students"))


@bp.post("/students/<int:uid>/delete")
@admin_required
def student_delete(uid):
    db.update("DELETE FROM users WHERE id=%s", (uid,))
    flash("Student and their exam attempts were deleted.", "ok")
    return redirect(url_for("admin.students"))


# ------------------------------------------------------------------ results
def _all_exams():
    return db.query("SELECT id, title FROM exams ORDER BY id DESC")


@bp.get("/results")
@admin_required
def results():
    all_exams = _all_exams()
    exam_id = request.args.get("exam", type=int)
    if not exam_id:
        blocks = db.query(
            "SELECT e.id, e.title, e.start_at, e.end_at, COUNT(a.id) AS total, "
            "SUM(a.submitted_at IS NOT NULL) AS done, SUM(a.verdict IN ('SUSPICIOUS','HIGH RISK')) AS flagged "
            "FROM exams e LEFT JOIN attempts a ON a.exam_id=e.id GROUP BY e.id, e.title, e.start_at, e.end_at ORDER BY e.id DESC")
        return render_template("admin/results_home.html", blocks=blocks)
    rows, exam = [], None
    verdict, q = request.args.get("verdict", ""), (request.args.get("q") or "").strip()
    if exam_id:
        exam = exams.exam_by_id(exam_id) or abort(404)
        exams.finalize_expired(exam_id)
        sql = ("SELECT a.*, u.name, u.roll, u.branch, u.year FROM attempts a JOIN users u ON u.id=a.user_id "
               "WHERE a.exam_id=%s")
        args = [exam_id]
        if verdict:
            sql += " AND a.verdict=%s"
            args.append(verdict)
        if q:
            sql += " AND (u.name LIKE %s OR u.roll LIKE %s)"
            args += [f"%{q}%"] * 2
        rows = db.query(sql + " ORDER BY LENGTH(u.roll), u.roll LIMIT 2000", tuple(args))
    return render_template("admin/results.html", exams=all_exams, exam=exam, rows=rows, f_verdict=verdict, f_q=q,
                           end_reasons=config.END_REASONS)


@bp.get("/attempts/<int:attempt_id>")
@admin_required
def attempt_detail(attempt_id):
    att = exams.load_attempt(attempt_id) or abort(404)
    exams.finalize_if_expired(att)
    att = exams.load_attempt(attempt_id)
    exam = exams.exam_by_id(att["exam_id"])
    user = db.one("SELECT * FROM users WHERE id=%s", (att["user_id"],))
    events = db.query("SELECT * FROM events WHERE attempt_id=%s ORDER BY ts, id", (attempt_id,))
    qmap = {q["id"]: q for q in exams.questions_of(att["exam_id"])}
    answers = []
    for qid in att["question_order"]:
        q = qmap.get(qid)
        if not q:
            continue
        chosen = att["answers"].get(str(qid))
        answers.append({"text": q["text"], "marks": q["marks"],
                        "chosen": q["options"][chosen] if chosen is not None and chosen < len(q["options"]) else None,
                        "correct": q["options"][q["correct_index"]], "is_correct": chosen == q["correct_index"]})
    pairs = similarity.for_attempt(att["exam_id"], attempt_id) if att["submitted_at"] else []
    return render_template("admin/attempt.html", att=att, exam=exam, user=user, events=events, answers=answers,
                           pairs=pairs, labels=config.EVENT_LABELS, end_reasons=config.END_REASONS)


@bp.post("/attempts/<int:attempt_id>/delete")
@admin_required
def attempt_delete(attempt_id):
    att = db.one("SELECT exam_id FROM attempts WHERE id=%s", (attempt_id,)) or abort(404)
    db.update("DELETE FROM attempts WHERE id=%s", (attempt_id,))
    flash("Attempt deleted. The student can take the exam again if it is still open.", "ok")
    return redirect(url_for("admin.results", exam=att["exam_id"]))


@bp.get("/exams/<int:exam_id>/similarity")
@admin_required
def exam_similarity(exam_id):
    exam = exams.exam_by_id(exam_id) or abort(404)
    exams.finalize_expired(exam_id)
    return render_template("admin/similarity.html", exam=exam, pairs=similarity.report(exam_id))


def _csv_safe(value):
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


@bp.get("/exams/<int:exam_id>/export.csv")
@admin_required
def export_csv(exam_id):
    exam = exams.exam_by_id(exam_id) or abort(404)
    exams.finalize_expired(exam_id)
    rows = db.query("SELECT a.*, u.name, u.roll, u.email, u.branch, u.year FROM attempts a "
                    "JOIN users u ON u.id=a.user_id WHERE a.exam_id=%s ORDER BY LENGTH(u.roll), u.roll", (exam_id,))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Roll No", "Name", "Gmail", "Branch", "Year", "Status", "Score", "Max score", "Percent", "Verdict",
                "Risk points", "Window violations", "Ended by", "Started (UTC)", "Submitted (UTC)"])
    iso = lambda t: datetime.utcfromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S") if t else ""
    for r in rows:
        score, mx = float(r["score"]), float(r["max_score"])
        w.writerow([_csv_safe(x) for x in (
            r["roll"], r["name"], r["email"], r["branch"], r["year"], "Submitted" if r["submitted_at"] else "In progress",
            score, mx, f"{100 * score / mx:.1f}" if mx else "", r["verdict"] or "", r["risk_points"], r["violations"],
            config.END_REASONS.get(r["end_reason"], r["end_reason"] or ""), iso(r["started_at"]), iso(r["submitted_at"]))])
    name = "".join(c if c.isalnum() else "_" for c in exam["title"])[:40]
    return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=results_{name}.csv"})


# ------------------------------------------------------------------ settings
@bp.get("/settings")
@admin_required
def settings():
    branches, years = _lists()
    return render_template("admin/settings.html", branches=", ".join(branches), years=", ".join(years),
                           mail_provider=mailer.provider(), dev_mode=mailer.dev_mode())


@bp.post("/settings/lists")
@admin_required
def settings_lists():
    branches = [b.strip() for b in request.form.get("branches", "").split(",") if b.strip()]
    years = [y.strip() for y in request.form.get("years", "").split(",") if y.strip()]
    if not branches or not years:
        flash("The branch and year lists cannot be empty.", "error")
    else:
        schema.set_setting("branches", branches)
        schema.set_setting("years", years)
        flash("Branch and year lists saved.", "ok")
    return redirect(url_for("admin.settings"))


@bp.post("/settings/password")
@admin_required
def settings_password():
    admin = db.one("SELECT * FROM admins WHERE id=%s", (g.admin["id"],))
    current, new, confirm = (request.form.get(k, "") for k in ("current", "new", "confirm"))
    if not security.verify_password(admin["password_hash"], current):
        flash("Your current password is incorrect.", "error")
    elif security.password_problem(new) or len(new) < 8:
        flash(security.password_problem(new) or "Use at least 8 characters for the admin password.", "error")
    elif new != confirm:
        flash("The two new passwords do not match.", "error")
    else:
        db.update("UPDATE admins SET password_hash=%s WHERE id=%s", (security.hash_password(new), admin["id"]))
        flash("Admin password changed.", "ok")
    return redirect(url_for("admin.settings"))


@bp.post("/settings/test-email")
@admin_required
def settings_test_email():
    to = security.normalize_email(request.form.get("to"))
    if not to or "@" not in to:
        flash("Enter an email address to send the test to.", "error")
    else:
        try:
            mailer.send_test(to)
            flash(f"Test email sent to {to}.", "ok")
        except mailer.MailError as e:
            flash(str(e), "error")
    return redirect(url_for("admin.settings"))
