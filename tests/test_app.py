"""Smoke tests. Needs a MySQL/MariaDB test database (set DB_* env vars, see README). Run: python -m pytest tests"""
import re
import time

import pytest

import db
import schema
from app import app as flask_app


@pytest.fixture()
def client():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        schema.init_db(force=True)
        for t in ("events", "attempts", "questions", "exams", "otps", "rate_limits", "users"):
            db.execute(f"DELETE FROM {t}")
        db.commit()
    return flask_app.test_client()


def csrf(c, path="/login"):
    html = c.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    if m is None:                       # already logged in: /login redirects, use the dashboard
        m = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/dashboard").get_data(as_text=True))
    return m.group(1)


def post_json(c, url, body):
    return c.post(url, json=body, headers={"X-CSRF-Token": csrf(c)})


def register(c, email, roll):
    r = post_json(c, "/api/register/send-otp", {"email": email})
    code = r.get_json()["dev_otp"]
    assert post_json(c, "/api/register/verify-otp", {"email": email, "code": code}).get_json()["ok"]
    r = post_json(c, "/api/register/complete", {"name": "Test User", "roll": roll, "branch": "CSE", "year": "1st Year",
                                                "password": "Passw0rd!x", "confirm": "Passw0rd!x"})
    assert r.get_json()["ok"], r.get_json()


def make_exam(minutes=10):
    t = int(time.time())
    with flask_app.app_context():
        eid = db.insert(
            "INSERT INTO exams(slug, title, description, duration_minutes, start_at, end_at, rules, min_read_seconds, "
            "shuffle_questions, shuffle_options, grace_seconds, allowed_warnings, block_copy, branches, years, is_active, created_at) "
            "VALUES ('slug1','T','',%s,%s,%s,'',0,1,1,5,0,1,'[]','[]',1,%s)", (minutes, t - 60, t + 3600, t))
        for i in range(3):
            db.insert("INSERT INTO questions(exam_id, position, text, options, correct_index, marks) VALUES (%s,%s,%s,%s,%s,1)",
                      (eid, i + 1, f"Q{i}", '["a","b","c"]', 1))
        db.commit()
    return eid


def test_register_login_and_single_device(client):
    register(client, "t1@gmail.com", "R1")
    other = flask_app.test_client()
    tok = csrf(other)
    r = other.post("/login", data={"roll": "R1", "password": "Passw0rd!x", "csrf_token": tok})
    assert r.status_code == 302
    assert client.get("/dashboard").status_code == 302          # first device was logged out


def test_wrong_password_and_csrf(client):
    register(client, "t2@gmail.com", "R2")
    assert client.post("/logout", data={}).status_code == 400   # no CSRF token
    other = flask_app.test_client()
    r = other.post("/login", data={"roll": "R2", "password": "bad", "csrf_token": csrf(other)})
    assert r.status_code == 401


def test_exam_flow_and_hidden_score(client):
    register(client, "t3@gmail.com", "R3")
    make_exam()
    assert client.get("/exam/slug1").status_code == 200
    tok = csrf(client, "/dashboard")
    r = client.post("/exam/slug1/begin", data={"agree": "on", "csrf_token": tok})
    assert r.status_code == 302
    aid = int(r.headers["Location"].rsplit("/", 1)[1])
    st = client.get(f"/api/attempt/{aid}/state").get_json()
    assert len(st["questions"]) == 3 and "correct" not in str(st)
    q = st["questions"][0]
    assert post_json(client, f"/api/attempt/{aid}/answer", {"qid": q["id"], "choice": 0}).get_json()["ok"]
    r = post_json(client, f"/api/attempt/{aid}/away", {"action": "start"})
    assert r.get_json()["status"] == "in_progress"              # short absence only logged
    r = post_json(client, f"/api/attempt/{aid}/submit", {})
    assert r.get_json()["status"] == "submitted"
    page = client.get(f"/attempt/{aid}/done").get_data(as_text=True)
    assert "Score" not in page and "/ 3" not in page


def test_long_away_auto_submits(client):
    register(client, "t4@gmail.com", "R4")
    make_exam()
    tok = csrf(client, "/dashboard")
    aid = int(client.post("/exam/slug1/begin", data={"agree": "on", "csrf_token": tok}).headers["Location"].rsplit("/", 1)[1])
    post_json(client, f"/api/attempt/{aid}/away", {"action": "start"})
    with flask_app.app_context():
        db.update("UPDATE attempts SET away_since=%s WHERE id=%s", ((int(time.time()) - 30) * 1000, aid))
        db.commit()
    r = post_json(client, f"/api/attempt/{aid}/away", {"action": "check"}).get_json()
    assert r["status"] == "submitted"
    with flask_app.app_context():
        assert db.one("SELECT end_reason FROM attempts WHERE id=%s", (aid,))["end_reason"] == "WINDOW_SWITCH"


def test_admin_login_and_csv(client):
    eid = make_exam()
    c = flask_app.test_client()
    r = c.post("/admin/login", data={"username": "admin", "password": "admin123", "csrf_token": csrf(c, "/admin/login")})
    assert r.status_code == 302
    assert c.get("/admin/results").status_code == 200
    assert "Roll No" in c.get("/admin/exams/" + str(eid) + "/export.csv").get_data(as_text=True)
