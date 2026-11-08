"""Database schema + first-run seeding."""
import json
import time

import config
import db
import security

TABLES = [
    """CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        email VARCHAR(190) NOT NULL UNIQUE,
        name VARCHAR(120) NOT NULL,
        roll VARCHAR(60) NOT NULL UNIQUE,
        branch VARCHAR(80) NOT NULL,
        year VARCHAR(40) NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        session_token VARCHAR(64) NULL,
        failed_logins INT NOT NULL DEFAULT 0,
        locked_until BIGINT NOT NULL DEFAULT 0,
        created_at BIGINT NOT NULL,
        last_login BIGINT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS admins (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(60) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        failed_logins INT NOT NULL DEFAULT 0,
        locked_until BIGINT NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS settings (
        k VARCHAR(60) PRIMARY KEY,
        v TEXT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS otps (
        id INT AUTO_INCREMENT PRIMARY KEY,
        email VARCHAR(190) NOT NULL,
        purpose VARCHAR(20) NOT NULL,
        code_hash VARCHAR(64) NOT NULL,
        salt VARCHAR(16) NOT NULL,
        expires_at BIGINT NOT NULL,
        attempts INT NOT NULL DEFAULT 0,
        last_sent_at BIGINT NOT NULL,
        sends INT NOT NULL DEFAULT 1,
        window_start BIGINT NOT NULL,
        UNIQUE KEY uq_otp (email, purpose)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS rate_limits (
        k VARCHAR(160) PRIMARY KEY,
        hits INT NOT NULL,
        reset_at BIGINT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS exams (
        id INT AUTO_INCREMENT PRIMARY KEY,
        slug VARCHAR(80) NOT NULL UNIQUE,
        title VARCHAR(200) NOT NULL,
        description TEXT,
        duration_minutes INT NOT NULL DEFAULT 30,
        start_at BIGINT NOT NULL,
        end_at BIGINT NOT NULL,
        rules TEXT,
        min_read_seconds INT NOT NULL DEFAULT 0,
        shuffle_questions TINYINT NOT NULL DEFAULT 1,
        shuffle_options TINYINT NOT NULL DEFAULT 1,
        grace_seconds INT NOT NULL DEFAULT 5,
        allowed_warnings INT NOT NULL DEFAULT 0,
        block_copy TINYINT NOT NULL DEFAULT 1,
        branches TEXT,
        years TEXT,
        is_active TINYINT NOT NULL DEFAULT 1,
        created_at BIGINT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS questions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        exam_id INT NOT NULL,
        position INT NOT NULL DEFAULT 0,
        text TEXT NOT NULL,
        options TEXT NOT NULL,
        correct_index INT NOT NULL,
        marks DECIMAL(6,2) NOT NULL DEFAULT 1,
        KEY ix_q_exam (exam_id),
        CONSTRAINT fk_q_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS attempts (
        id INT AUTO_INCREMENT PRIMARY KEY,
        exam_id INT NOT NULL,
        user_id INT NOT NULL,
        started_at BIGINT NOT NULL,
        deadline_at BIGINT NOT NULL,
        submitted_at BIGINT NULL,
        end_reason VARCHAR(40) NULL,
        score DECIMAL(8,2) NOT NULL DEFAULT 0,
        max_score DECIMAL(8,2) NOT NULL DEFAULT 0,
        question_order TEXT NOT NULL,
        option_order TEXT NOT NULL,
        answers TEXT NOT NULL,
        violations INT NOT NULL DEFAULT 0,
        away_since BIGINT NULL,
        risk_points INT NOT NULL DEFAULT 0,
        verdict VARCHAR(20) NULL,
        UNIQUE KEY uq_attempt (exam_id, user_id),
        CONSTRAINT fk_a_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
        CONSTRAINT fk_a_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS events (
        id INT AUTO_INCREMENT PRIMARY KEY,
        attempt_id INT NOT NULL,
        ts BIGINT NOT NULL,
        etype VARCHAR(40) NOT NULL,
        detail VARCHAR(255) NULL,
        points INT NOT NULL DEFAULT 0,
        KEY ix_e_attempt (attempt_id),
        CONSTRAINT fk_e_attempt FOREIGN KEY (attempt_id) REFERENCES attempts(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]

_ready = False


def init_db(force=False):
    """Create tables (if missing) and seed settings + the first admin. Safe to call repeatedly."""
    global _ready
    if _ready and not force:
        return
    for ddl in TABLES:
        db.execute(ddl)
    if db.scalar("SELECT COUNT(*) FROM settings") == 0:
        set_setting("branches", config.DEFAULT_BRANCHES)
        set_setting("years", config.DEFAULT_YEARS)
    if db.scalar("SELECT COUNT(*) FROM admins") == 0 and config.ADMIN_USERNAME and config.ADMIN_PASSWORD:
        db.insert("INSERT INTO admins(username, password_hash) VALUES (%s, %s)",
                  (config.ADMIN_USERNAME, security.hash_password(config.ADMIN_PASSWORD)))
    db.commit()
    _ready = True


def get_setting(key, default=None):
    raw = db.scalar("SELECT v FROM settings WHERE k=%s", (key,))
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def set_setting(key, value):
    raw = json.dumps(value, ensure_ascii=False)
    if db.update("UPDATE settings SET v=%s WHERE k=%s", (raw, key)) == 0:
        try:
            db.insert("INSERT INTO settings(k, v) VALUES (%s, %s)", (key, raw))
        except db.IntegrityError:
            db.update("UPDATE settings SET v=%s WHERE k=%s", (raw, key))


def now() -> int:
    return int(time.time())
