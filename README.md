# ProctorAI Web - Online Exam System with Cheating Detection

Flask + MySQL web app. Students use any phone or computer browser; the admin manages everything from `/admin`.

## Features
- **Students:** register once with Gmail + 6-digit OTP (Name, Roll No., Branch, Year, Password), then log in with Roll No. + password. Forgot password via Gmail OTP. One active device per Roll No.
- **Exams:** unlimited exams, each with its own link, questions, duration, start/end time, branch/year audience, rules and results.
- **Exam screen:** one question per page, Previous/Next + question palette, live progress bar, circular timer, autosave, browser crash recovery, faint name/roll watermark, large readable text, phone friendly.
- **Rules popup** before the exam (checkbox + optional minimum reading time). The timer starts only when the student presses *Continue & Begin Exam*.
- **Cheating detection:** leaving the exam window longer than the grace time (default 5 s) auto-submits the exam. Short interruptions (notification pop-ups) are only logged. Optional warnings before auto-submit. Copy / cut / right-click / developer-tools keys are blocked and logged. Same-wrong-answer similarity report. Risk score: CLEAN / SUSPICIOUS / HIGH RISK. Full activity timeline per student.
- **Admin:** dashboard with charts, exams (copy link, disable, new link, duplicate), questions (add/edit/delete/reorder, CSV/Excel import), students, results shown in **separate blocks per exam**, per-exam **CSV export**, results sorted by Roll No. ascending, similarity report, delete attempt (allows a retake), settings.
- Students never see their score.

## 1. Run locally (Windows + XAMPP)
1. Install Python 3.10+ and XAMPP. In XAMPP Control Panel start **MySQL** only (Apache is not needed).
   - If MySQL will not start: another program uses port 3306 (stop it, or change the port in XAMPP `my.ini`), run XAMPP as Administrator, or check `xampp\mysql\data\mysql_error.log`.
2. Open phpMyAdmin (`http://localhost/phpmyadmin` - needs Apache) or the MySQL shell and create a database named `proctorai` (utf8mb4). Tables are created automatically.
3. In the project folder:
   ```
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   copy .env.example .env
   python app.py
   ```
4. Open `http://localhost:5000` (students) and `http://localhost:5000/admin` (admin: `admin` / `admin123` in development - change it in Settings).
5. Optional: `python manage.py seed-demo` adds a demo exam. Run tests: `python -m pytest tests` (needs a separate empty test database; set `DB_NAME`).

In development the OTP is shown on the page (DEV MODE), so no email setup is needed to test.

To test on a phone on the same Wi-Fi, open `http://<your-PC-IP>:5000`.

## 2. Deploy online with Vercel
1. **Hosted MySQL** (Vercel has no MySQL). Create a free MySQL database at TiDB Cloud, Aiven or similar. Note host, port, user, password, database name. Hosted MySQL usually needs SSL: set `DB_SSL=true`.
2. **Email for OTPs** (production requires a real sender). Pick one:
   - Brevo (free): create an API key, verify your Gmail as a sender -> `BREVO_API_KEY`, `MAIL_FROM_EMAIL`.
   - Resend: needs a verified domain -> `RESEND_API_KEY`, `MAIL_FROM_EMAIL`.
   - Gmail SMTP with an App Password -> `SMTP_USER`, `SMTP_PASSWORD` (may be blocked on some hosts; the API options are more reliable).
3. Push this folder to a **GitHub** repository (`.env` is git-ignored).
4. On vercel.com: **Add New -> Project -> import the repo**. Framework preset: Other.
5. Add **Environment Variables**:
   | Name | Value |
   |---|---|
   | `APP_ENV` | `production` |
   | `SECRET_KEY` | long random string (`python -c "import secrets; print(secrets.token_hex(32))"`) |
   | `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | from your hosted MySQL |
   | `DB_SSL` | `true` |
   | `ADMIN_USERNAME`, `ADMIN_PASSWORD` | your first admin login (min 8 characters) |
   | mail variables | from step 2 |
6. **Deploy.** Open `https://<your-project>.vercel.app/admin`, log in, then Settings -> *Send test email*.
7. Create an exam, add questions, copy its link and share it with students.

Notes: Vercel's free Hobby plan is for non-commercial use. Tables are created on the first request.

## 3. Using the admin panel
1. **Exams -> New exam**: title, start/end time, duration, who can take it (branches/years), rules, grace time.
2. **Questions**: add manually or import CSV/Excel (download the template).
3. Copy the exam link and send it to students. Each exam has its own link and its own results block.
4. **Results**: choose an exam block -> filter, open a student for the activity log, export CSV, see the similarity report.

## Limits to know
A browser cannot see a second phone, a book or someone sitting next to the student, and detection works through the page focus/visibility APIs, so it is weaker than a desktop proctoring app. Use the logs and similarity report as evidence to review, not as proof. If a student logs in on another device during an exam, the first device is signed out and the event is logged as SECOND_LOGIN.
