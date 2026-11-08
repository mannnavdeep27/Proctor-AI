"""Command-line helpers.

    python manage.py init-db            create the tables (also happens automatically on first request)
    python manage.py create-admin NAME  create or reset an admin account (asks for a password)
    python manage.py seed-demo          add a demo exam with sample questions
"""
import getpass
import json
import sys
import time

import config
import db
import schema
import security
from services import exams


DEMO_QUESTIONS = [
    ("Which protocol secures web traffic?", ["HTTP", "HTTPS", "FTP", "Telnet"], 1),
    ("What does a firewall do?", ["Speeds up the internet", "Stores passwords", "Filters network traffic", "Removes viruses"], 2),
    ("Which of these is a strong password?", ["password123", "qwerty", "T7#kP9!vLm2q", "12345678"], 2),
    ("What is phishing?", ["A fishing sport", "Tricking people into giving up secrets", "A type of firewall", "A backup method"], 1),
    ("What does the 'A' in the CIA triad stand for?", ["Authentication", "Availability", "Authorization", "Accounting"], 1),
    ("Which attack floods a server with traffic?", ["DDoS", "SQL injection", "XSS", "Phishing"], 0),
    ("What does VPN stand for?", ["Virtual Private Network", "Very Private Node", "Verified Public Network", "Virtual Protocol Name"], 0),
    ("Which tool is commonly used to scan open ports?", ["Nmap", "Excel", "Photoshop", "Notepad"], 0),
]


def init_db():
    schema.init_db(force=True)
    print("Database is ready.")


def create_admin(username):
    password = getpass.getpass("New admin password (min 8 characters): ")
    if len(password) < 8 or password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords must match and be at least 8 characters.")
    schema.init_db()
    hashed = security.hash_password(password)
    if db.update("UPDATE admins SET password_hash=%s, failed_logins=0, locked_until=0 WHERE username=%s",
                 (hashed, username)) == 0:
        db.insert("INSERT INTO admins(username, password_hash) VALUES (%s, %s)", (username, hashed))
    db.commit()
    print(f"Admin '{username}' is ready.")


def seed_demo():
    schema.init_db()
    t = int(time.time())
    exam_id = db.insert(
        "INSERT INTO exams(slug, title, description, duration_minutes, start_at, end_at, rules, min_read_seconds, "
        "shuffle_questions, shuffle_options, grace_seconds, allowed_warnings, block_copy, branches, years, is_active, "
        "created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,1,5,0,1,'[]','[]',1,%s)",
        (exams.new_slug("Cyber Security Demo"), "Cyber Security Demo", "A short demo exam.", 10, t - 3600,
         t + 7 * 86400, "", 10, t))
    for pos, (text, options, answer) in enumerate(DEMO_QUESTIONS, 1):
        db.insert("INSERT INTO questions(exam_id, position, text, options, correct_index, marks) VALUES (%s,%s,%s,%s,%s,%s)",
                  (exam_id, pos, text, json.dumps(options), answer, 1))
    db.commit()
    print("Demo exam created.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "init-db":
        init_db()
    elif cmd == "create-admin" and len(sys.argv) > 2:
        create_admin(sys.argv[2])
    elif cmd == "seed-demo":
        seed_demo()
    else:
        print(__doc__)
