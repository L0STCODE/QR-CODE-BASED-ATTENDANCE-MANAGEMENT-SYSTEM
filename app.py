import csv
import os
import re
import socket
import sqlite3
import uuid
from io import BytesIO, StringIO
from urllib.parse import quote

from flask import Flask, Response, flash, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "development-only-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

DATABASE = os.environ.get("DATABASE_PATH", "cuz_attendance.db")
DEFAULT_SESSION_DURATION_MINUTES = 120
MIN_SESSION_DURATION_MINUTES = 5
MAX_SESSION_DURATION_MINUTES = 480
STUDENT_ID_PATTERN = re.compile(r"^\d{3}-\d{3}$")

DEFAULT_COURSES = [
    ("IT321", "Web Application Development", "Monday", "12:00", "14:00"),
    ("IT322", "Advanced Database Management Systems", "Friday", "10:00", "12:00"),
    ("IT323", "Software Engineering", "Thursday", "14:00", "16:00"),
    ("IT324", "Artificial Intelligence", "Tuesday", "14:00", "16:00"),
    ("IT325", "Human Computer Interactions", "Thursday", "16:00", "18:00"),
]

DEMO_STUDENTS = [
    ("110-174", "Demo Student One"),
    ("110-175", "Demo Student Two"),
    ("110-176", "Demo Student Three"),
]


def get_db():
    """Open the SQLite database and allow rows to be read by column name."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_column(cursor, table, column, definition):
    """Add a database column only if it does not already exist."""
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row["name"] for row in cursor.fetchall()]
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def logged_in():
    return "user" in session


def student_logged_in():
    return "student_id" in session


def current_user():
    return session.get("user")


def current_student_id():
    return session.get("student_id")


def safe_next_url(next_url):
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return None


def require_lecturer():
    if not logged_in():
        return redirect(url_for("lecturer_login"))
    return None


def require_student(next_url=None):
    if not student_logged_in():
        login_url = url_for("student_login", next=next_url) if next_url else url_for("student_login")
        return redirect(login_url)
    return None


def valid_student_id(student_id):
    return bool(STUDENT_ID_PATTERN.fullmatch(student_id))


def parse_session_duration(raw_duration):
    if not raw_duration:
        return DEFAULT_SESSION_DURATION_MINUTES

    try:
        duration = int(raw_duration)
    except ValueError:
        return None

    if MIN_SESSION_DURATION_MINUTES <= duration <= MAX_SESSION_DURATION_MINUTES:
        return duration
    return None


def expire_old_sessions(cursor):
    cursor.execute(
        """
        UPDATE sessions
        SET status='expired', closed_at=COALESCE(closed_at, CURRENT_TIMESTAMP)
        WHERE status='open'
          AND expires_at IS NOT NULL
          AND expires_at <= CURRENT_TIMESTAMP
        """
    )


def dashboard_stats(cursor):
    cursor.execute(
        """
        SELECT
            COUNT(*) AS total_sessions,
            SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_sessions,
            SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed_sessions,
            SUM(CASE WHEN status='expired' THEN 1 ELSE 0 END) AS expired_sessions
        FROM sessions
        WHERE lecturer_username=?
        """,
        (current_user(),),
    )
    stats = dict(cursor.fetchone())

    cursor.execute(
        """
        SELECT COUNT(attendance.id) AS total_attendance
        FROM attendance
        JOIN sessions ON attendance.session_code = sessions.session_code
        WHERE sessions.lecturer_username=?
        """,
        (current_user(),),
    )
    stats["total_attendance"] = cursor.fetchone()["total_attendance"]

    cursor.execute("SELECT COUNT(*) AS total_students FROM students")
    stats["total_students"] = cursor.fetchone()["total_students"]

    cursor.execute("SELECT COUNT(*) AS total_courses FROM courses")
    stats["total_courses"] = cursor.fetchone()["total_courses"]

    return {key: stats.get(key) or 0 for key in stats}


def laptop_ip_address():
    """Find the laptop Wi-Fi IP address so phones can open QR links."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def attendance_url_for(session_code):
    """Build the student scan link encoded into the QR code."""
    link = url_for("student_scan", session_code=session_code, _external=True)
    host = request.host.split(":")[0].lower()
    if host in {"localhost", "127.0.0.1"}:
        laptop_ip = laptop_ip_address()
        if laptop_ip:
            return link.replace(request.host, f"{laptop_ip}:5000", 1)
    return link


def can_access_session(cursor, session_code):
    cursor.execute(
        """
        SELECT sessions.*, courses.course_name
        FROM sessions
        LEFT JOIN courses ON sessions.course_code = courses.course_code
        WHERE sessions.session_code=?
          AND sessions.lecturer_username=?
        """,
        (session_code, current_user()),
    )
    return cursor.fetchone()


def get_student(cursor):
    cursor.execute(
        """
        SELECT *
        FROM students
        WHERE student_number=? OR student_id=?
        """,
        (current_student_id(), current_student_id()),
    )
    return cursor.fetchone()


# ------------------ DATABASE ------------------
def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            student_number TEXT,
            password_hash TEXT,
            programme TEXT DEFAULT 'BSc Computing',
            year INTEGER DEFAULT 3,
            semester INTEGER DEFAULT 2,
            mode TEXT DEFAULT 'Full-time',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_code TEXT UNIQUE NOT NULL,
            course_name TEXT NOT NULL,
            class_day TEXT,
            start_time TEXT,
            end_time TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS student_courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            UNIQUE(student_id, course_id),
            FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
            FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            course_code TEXT,
            session_code TEXT UNIQUE NOT NULL,
            lecturer_username TEXT,
            status TEXT DEFAULT 'open',
            duration_minutes INTEGER DEFAULT 120,
            expires_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            closed_at DATETIME
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            student_id TEXT NOT NULL,
            session_code TEXT NOT NULL,
            status TEXT DEFAULT 'Present',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    ensure_column(cursor, "students", "student_number", "TEXT")
    ensure_column(cursor, "students", "password_hash", "TEXT")
    ensure_column(cursor, "students", "programme", "TEXT DEFAULT 'BSc Computing'")
    ensure_column(cursor, "students", "year", "INTEGER DEFAULT 3")
    ensure_column(cursor, "students", "semester", "INTEGER DEFAULT 2")
    ensure_column(cursor, "students", "mode", "TEXT DEFAULT 'Full-time'")
    ensure_column(cursor, "students", "updated_at", "DATETIME DEFAULT CURRENT_TIMESTAMP")
    ensure_column(cursor, "courses", "class_day", "TEXT")
    ensure_column(cursor, "courses", "start_time", "TEXT")
    ensure_column(cursor, "courses", "end_time", "TEXT")
    ensure_column(cursor, "sessions", "course_code", "TEXT")
    ensure_column(cursor, "sessions", "lecturer_username", "TEXT")
    ensure_column(cursor, "sessions", "status", "TEXT DEFAULT 'open'")
    ensure_column(cursor, "sessions", "duration_minutes", "INTEGER DEFAULT 120")
    ensure_column(cursor, "sessions", "expires_at", "DATETIME")
    ensure_column(cursor, "sessions", "closed_at", "DATETIME")
    ensure_column(cursor, "attendance", "status", "TEXT DEFAULT 'Present'")

    cursor.execute("UPDATE sessions SET status='open' WHERE status IS NULL OR status=''")
    cursor.execute("UPDATE attendance SET status='Present' WHERE status IS NULL OR status=''")
    cursor.execute(
        "UPDATE sessions SET duration_minutes=? WHERE duration_minutes IS NULL",
        (DEFAULT_SESSION_DURATION_MINUTES,),
    )
    cursor.execute(
        """
        UPDATE sessions
        SET expires_at=datetime('now', '+' || duration_minutes || ' minutes')
        WHERE expires_at IS NULL
        """
    )
    expire_old_sessions(cursor)

    cursor.execute(
        """
        CREATE TRIGGER IF NOT EXISTS prevent_duplicate_attendance
        BEFORE INSERT ON attendance
        WHEN EXISTS (
            SELECT 1
            FROM attendance
            WHERE student_id = NEW.student_id
              AND session_code = NEW.session_code
        )
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_attendance');
        END
        """
    )

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_code ON sessions(session_code)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_lecturer ON sessions(lecturer_username)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_attendance_session ON attendance(session_code)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_attendance_student ON attendance(student_id)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_students_number ON students(student_number)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_courses_code ON courses(course_code)")

    for course_code, course_name, class_day, start_time, end_time in DEFAULT_COURSES:
        cursor.execute(
            """
            INSERT OR IGNORE INTO courses (
                course_code, course_name, class_day, start_time, end_time
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (course_code, course_name, class_day, start_time, end_time),
        )
        cursor.execute(
            """
            UPDATE courses
            SET course_name=?, class_day=?, start_time=?, end_time=?
            WHERE course_code=?
            """,
            (course_name, class_day, start_time, end_time, course_code),
        )

    for student_number, name in DEMO_STUDENTS:
        password_hash = generate_password_hash("1234")
        cursor.execute(
            """
            INSERT OR IGNORE INTO students (
                student_id, student_number, name, password_hash, programme, year, semester, mode
            )
            VALUES (?, ?, ?, ?, 'BSc Computing', 3, 2, 'Full-time')
            """,
            (student_number, student_number, name, password_hash),
        )
        cursor.execute(
            """
            UPDATE students
            SET student_number=?,
                name=?,
                password_hash=COALESCE(password_hash, ?),
                programme='BSc Computing',
                year=3,
                semester=2,
                mode='Full-time',
                updated_at=CURRENT_TIMESTAMP
            WHERE student_id=?
            """,
            (student_number, name, password_hash, student_number),
        )

    cursor.execute(
        "SELECT id FROM courses WHERE course_code IN (?, ?, ?, ?, ?)",
        [row[0] for row in DEFAULT_COURSES],
    )
    course_ids = [row["id"] for row in cursor.fetchall()]
    cursor.execute("SELECT id FROM students WHERE student_number IN (?, ?, ?)", [row[0] for row in DEMO_STUDENTS])
    student_ids = [row["id"] for row in cursor.fetchall()]

    for student_id in student_ids:
        for course_id in course_ids:
            cursor.execute(
                """
                INSERT OR IGNORE INTO student_courses (student_id, course_id)
                VALUES (?, ?)
                """,
                (student_id, course_id),
            )

    conn.commit()
    conn.close()


init_db()


# ------------------ AUTH ------------------
@app.route("/")
def home():
    if logged_in():
        return redirect(url_for("create_session"))
    if student_logged_in():
        return redirect(url_for("student_dashboard"))
    return render_template("index.html")


@app.route("/login")
def old_login():
    return redirect(url_for("home"))


@app.route("/lecturer/login", methods=["GET", "POST"])
def lecturer_login():
    if logged_in():
        return redirect(url_for("create_session"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Please enter your username and password.", "error")
            return redirect(url_for("lecturer_login"))

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username=?", (username,))
        user = cursor.fetchone()

        valid_password = False
        if user:
            stored_password = user["password"]
            try:
                valid_password = check_password_hash(stored_password, password)
            except ValueError:
                valid_password = False

            if not valid_password and stored_password == password:
                valid_password = True
                cursor.execute(
                    "UPDATE users SET password=? WHERE id=?",
                    (generate_password_hash(password), user["id"]),
                )
                conn.commit()

        conn.close()

        if user and valid_password:
            session.clear()
            session["user"] = username
            flash("Welcome back.", "success")
            return redirect(url_for("create_session"))

        flash("Invalid username or password.", "error")
        return redirect(url_for("lecturer_login"))

    return render_template("login.html")


@app.route("/student/login", methods=["GET", "POST"])
def student_login():
    next_url = safe_next_url(request.args.get("next", ""))

    if student_logged_in():
        return redirect(next_url or url_for("student_dashboard"))

    if request.method == "POST":
        student_number = request.form.get("student_number", "").strip()
        password = request.form.get("password", "")
        next_url = safe_next_url(request.form.get("next", ""))

        if not student_number or not password:
            flash("Please enter your student number and password.", "error")
            return redirect(url_for("student_login", next=next_url) if next_url else url_for("student_login"))

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM students
            WHERE student_number=? OR student_id=?
            """,
            (student_number, student_number),
        )
        student = cursor.fetchone()

        valid_password = False
        if student and student["password_hash"]:
            try:
                valid_password = check_password_hash(student["password_hash"], password)
            except ValueError:
                valid_password = False

        conn.close()

        if student and valid_password:
            session.clear()
            session["student_id"] = student["student_number"] or student["student_id"]
            session["student_name"] = student["name"]
            flash("Welcome back.", "success")
            return redirect(next_url or url_for("student_dashboard"))

        flash("Invalid student number or password.", "error")
        return redirect(url_for("student_login", next=next_url) if next_url else url_for("student_login"))

    return render_template("student.html", next_url=next_url or "")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        if not username or not password:
            flash("Please enter a username and password.", "error")
            return redirect(url_for("register"))

        conn = get_db()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (username, generate_password_hash(password)),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            flash("That username is already taken.", "error")
            return redirect(url_for("register"))
        finally:
            conn.close()

        flash("Account created. You can log in now.", "success")
        return redirect(url_for("lecturer_login"))

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))


# ------------------ LECTURER SESSIONS ------------------
@app.route("/create_session", methods=["GET", "POST"])
def create_session():
    redirect_response = require_lecturer()
    if redirect_response:
        return redirect_response

    conn = get_db()
    cursor = conn.cursor()
    expire_old_sessions(cursor)

    if request.method == "POST":
        session_name = request.form.get("session_name", "").strip()
        selected_course = request.form.get("existing_course", "").strip()
        typed_course = request.form.get("course_code", "").strip().upper()
        new_course_code = request.form.get("new_course_code", "").strip().upper()
        new_course_name = request.form.get("new_course_name", "").strip()
        duration_minutes = parse_session_duration(request.form.get("duration_minutes", "").strip())

        if not session_name:
            conn.close()
            flash("Please enter a session name.", "error")
            return redirect(url_for("create_session"))

        course_code = selected_course or typed_course
        if not course_code and new_course_code and new_course_name:
            cursor.execute(
                """
                INSERT OR IGNORE INTO courses (course_code, course_name)
                VALUES (?, ?)
                """,
                (new_course_code, new_course_name),
            )
            course_code = new_course_code

        if not course_code:
            conn.close()
            flash("Please select a course or type a new course code and name.", "error")
            return redirect(url_for("create_session"))

        if duration_minutes is None:
            conn.close()
            flash("Session duration must be between 5 and 480 minutes.", "error")
            return redirect(url_for("create_session"))

        session_code = uuid.uuid4().hex
        cursor.execute(
            """
            INSERT INTO sessions (
                name, course_code, session_code, lecturer_username,
                status, duration_minutes, expires_at
            )
            VALUES (?, ?, ?, ?, 'open', ?, datetime('now', '+' || ? || ' minutes'))
            """,
            (session_name, course_code, session_code, current_user(), duration_minutes, duration_minutes),
        )

        conn.commit()
        conn.close()

        flash("Session started. Share the QR code with students.", "success")
        return redirect(url_for("lecturer_dashboard", session_code=session_code))

    cursor.execute(
        """
        SELECT sessions.name,
               sessions.course_code,
               courses.course_name,
               sessions.session_code,
               sessions.status,
               sessions.duration_minutes,
               sessions.expires_at,
               sessions.created_at
        FROM sessions
        LEFT JOIN courses ON sessions.course_code = courses.course_code
        WHERE sessions.lecturer_username=?
        ORDER BY sessions.created_at DESC
        LIMIT 8
        """,
        (current_user(),),
    )
    sessions_list = cursor.fetchall()
    cursor.execute("SELECT course_code, course_name FROM courses ORDER BY course_code")
    courses = cursor.fetchall()
    stats = dashboard_stats(cursor)
    conn.commit()
    conn.close()

    return render_template("create_session.html", sessions=sessions_list, courses=courses, stats=stats)


@app.route("/lecturer/<session_code>")
def lecturer_dashboard(session_code):
    redirect_response = require_lecturer()
    if redirect_response:
        return redirect_response

    conn = get_db()
    cursor = conn.cursor()
    expire_old_sessions(cursor)
    session_data = can_access_session(cursor, session_code)

    if not session_data:
        conn.close()
        flash("That session could not be found.", "error")
        return redirect(url_for("create_session"))

    cursor.execute(
        """
        SELECT name, student_id, status, timestamp
        FROM attendance
        WHERE session_code=?
        ORDER BY timestamp DESC
        """,
        (session_code,),
    )
    records = cursor.fetchall()
    conn.commit()
    conn.close()

    return render_template(
        "lecturer.html",
        session_name=session_data["name"],
        course_code=session_data["course_code"],
        course_name=session_data["course_name"],
        session_code=session_code,
        status=session_data["status"],
        created_at=session_data["created_at"],
        expires_at=session_data["expires_at"],
        duration_minutes=session_data["duration_minutes"],
        records=records,
        attendance_url=attendance_url_for(session_code),
        qr_image_url=url_for("qr_code_image", session_code=session_code),
    )


@app.route("/qr/<session_code>.png")
def qr_code_image(session_code):
    redirect_response = require_lecturer()
    if redirect_response:
        return redirect_response

    conn = get_db()
    cursor = conn.cursor()
    expire_old_sessions(cursor)
    session_data = can_access_session(cursor, session_code)
    conn.commit()
    conn.close()

    if not session_data:
        flash("That session could not be found.", "error")
        return redirect(url_for("create_session"))

    attendance_url = attendance_url_for(session_code)

    try:
        import qrcode
    except ImportError:
        fallback_url = "https://api.qrserver.com/v1/create-qr-code/?size=240x240&data="
        return redirect(fallback_url + quote(attendance_url))

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(attendance_url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#073d73", back_color="white")

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)

    return send_file(buffer, mimetype="image/png")


@app.route("/session/<session_code>/close", methods=["POST"])
def close_session(session_code):
    redirect_response = require_lecturer()
    if redirect_response:
        return redirect_response

    conn = get_db()
    cursor = conn.cursor()
    expire_old_sessions(cursor)
    session_data = can_access_session(cursor, session_code)

    if not session_data:
        conn.close()
        flash("That session could not be found.", "error")
        return redirect(url_for("create_session"))

    cursor.execute(
        """
        UPDATE sessions
        SET status='closed', closed_at=CURRENT_TIMESTAMP
        WHERE session_code=?
        """,
        (session_code,),
    )
    conn.commit()
    conn.close()

    flash("Session closed. Students can no longer submit attendance for it.", "success")
    return redirect(url_for("lecturer_dashboard", session_code=session_code))


@app.route("/session/<session_code>/delete", methods=["POST"])
def delete_session(session_code):
    redirect_response = require_lecturer()
    if redirect_response:
        return redirect_response

    conn = get_db()
    cursor = conn.cursor()
    expire_old_sessions(cursor)
    session_data = can_access_session(cursor, session_code)

    if not session_data:
        conn.close()
        flash("That session could not be found.", "error")
        return redirect(url_for("create_session"))

    cursor.execute("DELETE FROM attendance WHERE session_code=?", (session_code,))
    cursor.execute("DELETE FROM sessions WHERE session_code=?", (session_code,))
    conn.commit()
    conn.close()

    flash("Lecture session deleted.", "success")
    return redirect(url_for("create_session"))


# ------------------ STUDENTS ------------------
@app.route("/student/scan/<session_code>", methods=["GET", "POST"])
def student_scan(session_code):
    return mark_attendance(session_code)


@app.route("/mark/<session_code>", methods=["GET", "POST"])
def mark_attendance(session_code):
    next_url = url_for("student_scan", session_code=session_code)
    redirect_response = require_student(next_url)
    if redirect_response:
        return redirect_response

    conn = get_db()
    cursor = conn.cursor()
    expire_old_sessions(cursor)

    cursor.execute(
        """
        SELECT sessions.*, courses.course_name
        FROM sessions
        LEFT JOIN courses ON sessions.course_code = courses.course_code
        WHERE sessions.session_code=?
        """,
        (session_code,),
    )
    session_data = cursor.fetchone()

    if not session_data:
        conn.close()
        flash("This attendance session does not exist.", "error")
        return redirect(url_for("student_dashboard"))

    student = get_student(cursor)
    if not student:
        session.clear()
        conn.close()
        flash("Please log in as a student again.", "error")
        return redirect(url_for("student_login", next=next_url))

    if session_data["status"] != "open":
        conn.commit()
        conn.close()
        return render_template(
            "success.html",
            result="expired" if session_data["status"] == "expired" else "closed",
            session_name=session_data["name"],
            course_code=session_data["course_code"],
            course_name=session_data["course_name"],
            session_status=session_data["status"],
        )

    student_number = student["student_number"] or student["student_id"]
    cursor.execute(
        """
        SELECT 1
        FROM student_courses
        JOIN courses ON student_courses.course_id = courses.id
        WHERE student_courses.student_id=?
          AND courses.course_code=?
        """,
        (student["id"], session_data["course_code"]),
    )
    if not cursor.fetchone():
        conn.close()
        return render_template(
            "success.html",
            result="not_enrolled",
            session_name=session_data["name"],
            course_code=session_data["course_code"],
            course_name=session_data["course_name"],
            session_status=session_data["status"],
        )

    cursor.execute(
        """
        SELECT timestamp
        FROM attendance
        WHERE student_id=? AND session_code=?
        """,
        (student_number, session_code),
    )
    existing = cursor.fetchone()

    if existing:
        conn.close()
        return render_template(
            "success.html",
            result="already",
            student_name=student["name"],
            student_number=student_number,
            course_code=session_data["course_code"],
            course_name=session_data["course_name"],
            session_name=session_data["name"],
            marked_time=existing["timestamp"],
        )

    try:
        cursor.execute(
            """
            INSERT INTO attendance (name, student_id, session_code, status)
            VALUES (?, ?, ?, 'Present')
            """,
            (student["name"], student_number, session_code),
        )
        cursor.execute(
            """
            SELECT timestamp
            FROM attendance
            WHERE student_id=? AND session_code=?
            """,
            (student_number, session_code),
        )
        new_record = cursor.fetchone()
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        cursor.execute(
            """
            SELECT timestamp
            FROM attendance
            WHERE student_id=? AND session_code=?
            """,
            (student_number, session_code),
        )
        new_record = cursor.fetchone()
        conn.close()
        return render_template(
            "success.html",
            result="already",
            student_name=student["name"],
            student_number=student_number,
            course_code=session_data["course_code"],
            course_name=session_data["course_name"],
            session_name=session_data["name"],
            marked_time=new_record["timestamp"] if new_record else "",
        )

    conn.close()
    return render_template(
        "success.html",
        result="success",
        student_name=student["name"],
        student_number=student_number,
        course_code=session_data["course_code"],
        course_name=session_data["course_name"],
        session_name=session_data["name"],
        marked_time=new_record["timestamp"] if new_record else "",
    )


@app.route("/student/dashboard")
def student_dashboard():
    redirect_response = require_student()
    if redirect_response:
        return redirect_response

    conn = get_db()
    cursor = conn.cursor()
    expire_old_sessions(cursor)
    student = get_student(cursor)

    if not student:
        session.clear()
        conn.close()
        flash("Please log in as a student again.", "error")
        return redirect(url_for("student_login"))

    student_number = student["student_number"] or student["student_id"]
    cursor.execute(
        """
        SELECT courses.id,
               courses.course_code,
               courses.course_name,
               courses.class_day,
               courses.start_time,
               courses.end_time,
               (
                   SELECT session_code
                   FROM sessions
                   WHERE sessions.course_code = courses.course_code
                     AND sessions.status='open'
                   ORDER BY sessions.created_at DESC
                   LIMIT 1
               ) AS active_session_code,
               (
                   SELECT name
                   FROM sessions
                   WHERE sessions.course_code = courses.course_code
                     AND sessions.status='open'
                   ORDER BY sessions.created_at DESC
                   LIMIT 1
               ) AS active_session_name
        FROM courses
        JOIN student_courses ON courses.id = student_courses.course_id
        WHERE student_courses.student_id=?
        ORDER BY courses.course_code
        """,
        (student["id"],),
    )
    courses = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) AS total FROM attendance WHERE student_id=? AND status='Present'", (student_number,))
    present_count = cursor.fetchone()["total"] or 0

    cursor.execute(
        """
        SELECT COUNT(*) AS total_absent
        FROM sessions
        WHERE sessions.course_code IN (
            SELECT courses.course_code
            FROM courses
            JOIN student_courses ON courses.id = student_courses.course_id
            WHERE student_courses.student_id=?
        )
        AND NOT EXISTS (
            SELECT 1
            FROM attendance
            WHERE attendance.session_code = sessions.session_code
              AND attendance.student_id=?
        )
        """,
        (student["id"], student_number),
    )
    absent_count = cursor.fetchone()["total_absent"] or 0
    late_count = 0
    total_sessions = present_count + absent_count
    attendance_percentage = round((present_count / total_sessions) * 100) if total_sessions else 0

    cursor.execute(
        """
        SELECT attendance.timestamp,
               attendance.status,
               sessions.name AS session_name,
               sessions.course_code,
               courses.course_name
        FROM attendance
        JOIN sessions ON attendance.session_code = sessions.session_code
        LEFT JOIN courses ON sessions.course_code = courses.course_code
        WHERE attendance.student_id=?
        ORDER BY attendance.timestamp DESC
        LIMIT 8
        """,
        (student_number,),
    )
    recent_records = cursor.fetchall()
    initials = "".join(part[0] for part in student["name"].split()[:2]).upper()

    conn.commit()
    conn.close()

    return render_template(
        "student_dashboard.html",
        student=student,
        student_number=student_number,
        initials=initials,
        courses=courses,
        present_count=present_count,
        late_count=late_count,
        absent_count=absent_count,
        attendance_percentage=attendance_percentage,
        recent_records=recent_records,
    )


@app.route("/student/attendance")
def student_attendance_report():
    redirect_response = require_student()
    if redirect_response:
        return redirect_response

    conn = get_db()
    cursor = conn.cursor()
    expire_old_sessions(cursor)
    student = get_student(cursor)

    if not student:
        session.clear()
        conn.close()
        flash("Please log in as a student again.", "error")
        return redirect(url_for("student_login"))

    student_number = student["student_number"] or student["student_id"]
    cursor.execute("SELECT COUNT(*) AS total FROM attendance WHERE student_id=? AND status='Present'", (student_number,))
    present_count = cursor.fetchone()["total"] or 0

    cursor.execute(
        """
        SELECT COUNT(*) AS total_absent
        FROM sessions
        WHERE sessions.course_code IN (
            SELECT courses.course_code
            FROM courses
            JOIN student_courses ON courses.id = student_courses.course_id
            WHERE student_courses.student_id=?
        )
        AND NOT EXISTS (
            SELECT 1
            FROM attendance
            WHERE attendance.session_code = sessions.session_code
              AND attendance.student_id=?
        )
        """,
        (student["id"], student_number),
    )
    absent_count = cursor.fetchone()["total_absent"] or 0
    late_count = 0
    total_sessions = present_count + absent_count
    attendance_percentage = round((present_count / total_sessions) * 100) if total_sessions else 0

    cursor.execute(
        """
        SELECT attendance.timestamp,
               attendance.status,
               sessions.course_code,
               sessions.name AS session_name,
               courses.course_name
        FROM attendance
        JOIN sessions ON attendance.session_code = sessions.session_code
        LEFT JOIN courses ON sessions.course_code = courses.course_code
        WHERE attendance.student_id=?
        ORDER BY attendance.timestamp DESC
        """,
        (student_number,),
    )
    records = cursor.fetchall()
    initials = "".join(part[0] for part in student["name"].split()[:2]).upper()

    conn.commit()
    conn.close()

    return render_template(
        "student_attendance.html",
        student=student,
        student_number=student_number,
        initials=initials,
        present_count=present_count,
        late_count=late_count,
        absent_count=absent_count,
        attendance_percentage=attendance_percentage,
        records=records,
    )


# ------------------ COURSE AND STUDENT MANAGEMENT ------------------
@app.route("/students")
def view_students():
    redirect_response = require_lecturer()
    if redirect_response:
        return redirect_response

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT students.id,
               students.name,
               students.student_number,
               students.programme,
               students.year,
               students.semester,
               students.mode,
               COUNT(DISTINCT attendance.id) AS attendance_count
        FROM students
        LEFT JOIN attendance ON attendance.student_id = students.student_number
        GROUP BY students.id
        ORDER BY students.name
        """
    )
    students = cursor.fetchall()

    student_courses = {}
    cursor.execute(
        """
        SELECT students.id AS student_id,
               courses.course_code,
               courses.course_name
        FROM students
        JOIN student_courses ON students.id = student_courses.student_id
        JOIN courses ON student_courses.course_id = courses.id
        ORDER BY courses.course_code
        """
    )
    for row in cursor.fetchall():
        student_courses.setdefault(row["student_id"], []).append(row)

    conn.close()
    return render_template("students.html", students=students, student_courses=student_courses)


@app.route("/students/add", methods=["GET", "POST"])
def add_student():
    redirect_response = require_lecturer()
    if redirect_response:
        return redirect_response

    conn = get_db()
    cursor = conn.cursor()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        student_number = request.form.get("student_number", "").strip()
        password = request.form.get("password", "")
        programme = request.form.get("programme", "BSc Computing").strip() or "BSc Computing"
        year = request.form.get("year", "3").strip() or "3"
        semester = request.form.get("semester", "2").strip() or "2"
        mode = request.form.get("mode", "Full-time").strip() or "Full-time"
        selected_courses = request.form.getlist("course_ids")
        new_course_code = request.form.get("new_course_code", "").strip().upper()
        new_course_name = request.form.get("new_course_name", "").strip()

        if not name or not student_number or not password:
            conn.close()
            flash("Please enter the student name, student number, and password.", "error")
            return redirect(url_for("add_student"))

        if not valid_student_id(student_number):
            conn.close()
            flash("Student number must use the XXX-XXX format, for example 110-174.", "error")
            return redirect(url_for("add_student"))

        cursor.execute(
            """
            SELECT id
            FROM students
            WHERE student_number=? OR student_id=?
            """,
            (student_number, student_number),
        )
        if cursor.fetchone():
            conn.close()
            flash("That student number already exists.", "error")
            return redirect(url_for("add_student"))

        cursor.execute(
            """
            INSERT INTO students (
                student_id, student_number, name, password_hash, programme, year, semester, mode
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                student_number,
                student_number,
                name,
                generate_password_hash(password),
                programme,
                year,
                semester,
                mode,
            ),
        )
        new_student_id = cursor.lastrowid

        if new_course_code and new_course_name:
            cursor.execute(
                """
                INSERT OR IGNORE INTO courses (course_code, course_name)
                VALUES (?, ?)
                """,
                (new_course_code, new_course_name),
            )
            cursor.execute("SELECT id FROM courses WHERE course_code=?", (new_course_code,))
            new_course = cursor.fetchone()
            if new_course:
                selected_courses.append(str(new_course["id"]))

        for course_id in selected_courses:
            cursor.execute(
                """
                INSERT OR IGNORE INTO student_courses (student_id, course_id)
                VALUES (?, ?)
                """,
                (new_student_id, course_id),
            )

        conn.commit()
        conn.close()

        flash("Student added successfully.", "success")
        return redirect(url_for("add_student"))

    cursor.execute("SELECT id, course_code, course_name FROM courses ORDER BY course_code")
    courses = cursor.fetchall()
    conn.close()
    return render_template("add_student.html", courses=courses)


@app.route("/courses", methods=["GET", "POST"])
def manage_courses():
    redirect_response = require_lecturer()
    if redirect_response:
        return redirect_response

    conn = get_db()
    cursor = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action", "add")
        course_code = request.form.get("course_code", "").strip().upper()
        course_name = request.form.get("course_name", "").strip()
        class_day = request.form.get("class_day", "").strip()
        start_time = request.form.get("start_time", "").strip()
        end_time = request.form.get("end_time", "").strip()

        if action in {"add", "edit"} and (not course_code or not course_name):
            conn.close()
            flash("Please enter the course code and course name.", "error")
            return redirect(url_for("manage_courses"))

        if action == "add":
            try:
                cursor.execute(
                    """
                    INSERT INTO courses (course_code, course_name, class_day, start_time, end_time)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (course_code, course_name, class_day, start_time, end_time),
                )
                conn.commit()
                flash("Course added successfully.", "success")
            except sqlite3.IntegrityError:
                flash("That course code already exists.", "error")

        elif action == "edit":
            cursor.execute(
                """
                UPDATE courses
                SET course_name=?, class_day=?, start_time=?, end_time=?
                WHERE course_code=?
                """,
                (course_name, class_day, start_time, end_time, course_code),
            )
            conn.commit()
            flash("Course updated successfully.", "success")

        elif action == "delete":
            cursor.execute("SELECT COUNT(*) AS total FROM sessions WHERE course_code=?", (course_code,))
            session_count = cursor.fetchone()["total"]
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM attendance
                JOIN sessions ON attendance.session_code = sessions.session_code
                WHERE sessions.course_code=?
                """,
                (course_code,),
            )
            attendance_count = cursor.fetchone()["total"]
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM student_courses
                JOIN courses ON student_courses.course_id = courses.id
                WHERE courses.course_code=?
                """,
                (course_code,),
            )
            enrollment_count = cursor.fetchone()["total"]

            if session_count or attendance_count or enrollment_count:
                flash("This course is linked to sessions, attendance, or students, so it cannot be deleted safely.", "error")
            else:
                cursor.execute("DELETE FROM courses WHERE course_code=?", (course_code,))
                conn.commit()
                flash("Course deleted successfully.", "success")

        conn.close()
        return redirect(url_for("manage_courses"))

    cursor.execute(
        """
        SELECT courses.*,
               COUNT(DISTINCT student_courses.student_id) AS student_count,
               COUNT(DISTINCT sessions.id) AS session_count
        FROM courses
        LEFT JOIN student_courses ON courses.id = student_courses.course_id
        LEFT JOIN sessions ON courses.course_code = sessions.course_code
        GROUP BY courses.id
        ORDER BY courses.course_code
        """
    )
    courses = cursor.fetchall()
    conn.close()

    return render_template("courses.html", courses=courses)


# ------------------ LIVE DATA ------------------
@app.route("/attendance_data/<session_code>")
def attendance_data(session_code):
    if not logged_in():
        return {"records": []}, 403

    conn = get_db()
    cursor = conn.cursor()
    expire_old_sessions(cursor)
    session_data = can_access_session(cursor, session_code)

    if not session_data:
        conn.commit()
        conn.close()
        return {"records": []}, 404

    cursor.execute(
        """
        SELECT name, student_id, status, timestamp
        FROM attendance
        WHERE session_code=?
        ORDER BY timestamp DESC
        """,
        (session_code,),
    )
    records = cursor.fetchall()
    conn.commit()
    conn.close()

    return {"records": [list(record) for record in records]}


# ------------------ REPORTS ------------------
def fetch_attendance_report(cursor, selected_session=None, selected_course=None):
    params = [current_user()]
    where = "sessions.lecturer_username=?"

    if selected_session:
        where += " AND attendance.session_code=?"
        params.append(selected_session)

    if selected_course:
        where += " AND sessions.course_code=?"
        params.append(selected_course)

    cursor.execute(
        f"""
        SELECT attendance.id,
               attendance.name,
               attendance.student_id,
               sessions.name AS session_name,
               sessions.course_code,
               courses.course_name,
               sessions.session_code,
               sessions.status AS session_status,
               attendance.status AS attendance_status,
               sessions.expires_at,
               attendance.timestamp
        FROM attendance
        LEFT JOIN sessions ON attendance.session_code = sessions.session_code
        LEFT JOIN courses ON sessions.course_code = courses.course_code
        WHERE {where}
        ORDER BY attendance.timestamp DESC
        """,
        params,
    )
    return cursor.fetchall()


def attendance_report_response(rows, filename):
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Record ID",
            "Student Name",
            "Student ID",
            "Lecture Session",
            "Course Code",
            "Course Name",
            "Session Code",
            "Session Status",
            "Attendance Status",
            "Expires At",
            "Marked At",
        ]
    )

    for row in rows:
        writer.writerow(
            [
                row["id"],
                row["name"],
                row["student_id"],
                row["session_name"] or "Unknown session",
                row["course_code"] or "",
                row["course_name"] or "",
                row["session_code"],
                row["session_status"] or "open",
                row["attendance_status"] or "Present",
                row["expires_at"] or "",
                row["timestamp"],
            ]
        )

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/reports/attendance.csv")
def export_attendance_report():
    redirect_response = require_lecturer()
    if redirect_response:
        return redirect_response

    selected_session = request.args.get("session_code", "").strip() or None
    selected_course = request.args.get("course_code", "").strip() or None
    conn = get_db()
    cursor = conn.cursor()
    expire_old_sessions(cursor)

    if selected_session and not can_access_session(cursor, selected_session):
        conn.close()
        flash("You do not have access to that session report.", "error")
        return redirect(url_for("view_records"))

    rows = fetch_attendance_report(cursor, selected_session, selected_course)
    conn.commit()
    conn.close()

    filename = "cuz-attendance-report.csv"
    if selected_session:
        filename = f"cuz-attendance-{selected_session[:8]}.csv"

    return attendance_report_response(rows, filename)


@app.route("/reports/session/<session_code>.csv")
def export_session_report(session_code):
    redirect_response = require_lecturer()
    if redirect_response:
        return redirect_response

    conn = get_db()
    cursor = conn.cursor()
    expire_old_sessions(cursor)

    if not can_access_session(cursor, session_code):
        conn.close()
        flash("You do not have access to that session report.", "error")
        return redirect(url_for("view_records"))

    rows = fetch_attendance_report(cursor, session_code)
    conn.commit()
    conn.close()

    return attendance_report_response(rows, f"cuz-attendance-{session_code[:8]}.csv")


@app.route("/admin")
def admin():
    redirect_response = require_lecturer()
    if redirect_response:
        return redirect_response
    return redirect(url_for("create_session"))


@app.route("/view")
def view_records():
    redirect_response = require_lecturer()
    if redirect_response:
        return redirect_response

    selected_session = request.args.get("session_code", "").strip()
    selected_course = request.args.get("course_code", "").strip()

    conn = get_db()
    cursor = conn.cursor()
    expire_old_sessions(cursor)

    cursor.execute(
        """
        SELECT sessions.name,
               sessions.course_code,
               courses.course_name,
               sessions.session_code,
               sessions.status,
               sessions.expires_at,
               sessions.created_at
        FROM sessions
        LEFT JOIN courses ON sessions.course_code = courses.course_code
        WHERE sessions.lecturer_username=?
        ORDER BY sessions.created_at DESC
        """,
        (current_user(),),
    )
    sessions_list = cursor.fetchall()

    cursor.execute("SELECT course_code, course_name FROM courses ORDER BY course_code")
    courses = cursor.fetchall()

    if selected_session and not can_access_session(cursor, selected_session):
        conn.close()
        flash("You do not have access to that session.", "error")
        return redirect(url_for("view_records"))

    records = fetch_attendance_report(cursor, selected_session or None, selected_course or None)
    conn.commit()
    conn.close()

    return render_template(
        "view.html",
        records=records,
        sessions=sessions_list,
        courses=courses,
        selected_session=selected_session,
        selected_course=selected_course,
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
