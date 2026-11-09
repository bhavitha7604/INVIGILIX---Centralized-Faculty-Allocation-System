
from flask import (
    Flask, render_template, request,
    redirect, url_for, session, flash, make_response
)
import sqlite3
import os
import csv
import io

from datetime import datetime
from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

# =====================================================
# 1. APPLICATION CONFIGURATION
# =====================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "change_this_to_a_secure_random_secret_key"
)

DATABASE = os.path.join(
    app.root_path,
    "examination.db"
)

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

DAYS = [
    "Monday", "Tuesday", "Wednesday",
    "Thursday", "Friday", "Saturday"
]


# =====================================================
# 2. DATABASE CONNECTION
# =====================================================

def get_db_connection():

    conn = sqlite3.connect(
        DATABASE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")

    return conn


# =====================================================
# 3. DATABASE TABLES AND MIGRATIONS
# =====================================================

def create_table():

    conn = get_db_connection()

    try:

        conn.execute("PRAGMA journal_mode = WAL")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS faculty (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                department TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL,
                username TEXT UNIQUE,
                password_hash TEXT
            )
        """)

        faculty_columns = [
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(faculty)"
            ).fetchall()
        ]

        if "username" not in faculty_columns:
            conn.execute(
                "ALTER TABLE faculty ADD COLUMN username TEXT"
            )

        if "password_hash" not in faculty_columns:
            conn.execute(
                "ALTER TABLE faculty ADD COLUMN password_hash TEXT"
            )

        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_faculty_username
            ON faculty(username)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS timetable (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                faculty_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                subject TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                semester TEXT,
                FOREIGN KEY (faculty_id)
                    REFERENCES faculty(id)
                    ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS examinations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exam_name TEXT NOT NULL,
                subject TEXT NOT NULL,
                department TEXT NOT NULL DEFAULT '',
                semester TEXT DEFAULT '',
                exam_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                student_count INTEGER DEFAULT 0
            )
        """)

        exam_columns = [
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(examinations)"
            ).fetchall()
        ]

        if "department" not in exam_columns:
            conn.execute("""
                ALTER TABLE examinations
                ADD COLUMN department TEXT NOT NULL DEFAULT ''
            """)

        if "semester" not in exam_columns:
            conn.execute("""
                ALTER TABLE examinations
                ADD COLUMN semester TEXT DEFAULT ''
            """)

        if "student_count" not in exam_columns:
            conn.execute("""
                ALTER TABLE examinations
                ADD COLUMN student_count INTEGER DEFAULT 0
            """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS halls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hall_number TEXT NOT NULL UNIQUE,
                building_name TEXT NOT NULL,
                seating_capacity INTEGER NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS duty_allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exam_id INTEGER NOT NULL,
                hall_id INTEGER NOT NULL,
                faculty_id INTEGER NOT NULL,
                duty_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                FOREIGN KEY (exam_id)
                    REFERENCES examinations(id),
                FOREIGN KEY (hall_id)
                    REFERENCES halls(id),
                FOREIGN KEY (faculty_id)
                    REFERENCES faculty(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                faculty_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                FOREIGN KEY (faculty_id)
                    REFERENCES faculty(id)
                    ON DELETE CASCADE
            )
        """)

        conn.commit()

    finally:
        conn.close()


create_table()


# =====================================================
# 4. HELPER FUNCTIONS
# =====================================================

def admin_required():

    if session.get("role") != "admin":
        flash("Please login as admin to continue.", "error")
        return False

    return True


def faculty_required():

    if session.get("role") != "faculty":
        flash("Please login as faculty to continue.", "error")
        return False

    return True


def convert_to_24_hour(time_value):

    if not time_value:
        return None

    time_value = time_value.strip()

    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p"):

        try:
            return datetime.strptime(
                time_value, fmt
            ).strftime("%H:%M")

        except ValueError:
            continue

    return None


def convert_to_12_hour(time_value):

    if not time_value:
        return ""

    try:
        return datetime.strptime(
            time_value, "%H:%M"
        ).strftime("%I:%M %p")

    except (ValueError, TypeError):
        return time_value


app.jinja_env.filters["time12"] = convert_to_12_hour


def valid_date(date_value):

    try:
        datetime.strptime(date_value, "%Y-%m-%d")
        return True

    except (ValueError, TypeError):
        return False


def valid_time_range(start_time, end_time):

    if not start_time or not end_time:
        return False

    return start_time < end_time


def has_time_overlap(
    existing_start,
    existing_end,
    new_start,
    new_end
):

    return (
        new_start < existing_end
        and new_end > existing_start
    )


def get_filtered_report_data(
    conn,
    exam_id=None,
    faculty_id=None,
    duty_date=None
):

    query = """
        SELECT
            d.id,
            d.exam_id,
            d.hall_id,
            d.faculty_id,
            d.duty_date,
            d.start_time,
            d.end_time,
            e.exam_name,
            e.subject,
            e.department,
            e.semester,
            f.name AS faculty_name,
            f.department AS faculty_department,
            h.hall_number,
            h.building_name
        FROM duty_allocations d
        JOIN examinations e ON d.exam_id = e.id
        JOIN faculty f ON d.faculty_id = f.id
        JOIN halls h ON d.hall_id = h.id
        WHERE 1 = 1
    """

    params = []

    if exam_id:
        query += " AND d.exam_id = ?"
        params.append(exam_id)

    if faculty_id:
        query += " AND d.faculty_id = ?"
        params.append(faculty_id)

    if duty_date:
        query += " AND d.duty_date = ?"
        params.append(duty_date)

    query += """
        ORDER BY d.duty_date DESC, d.start_time ASC
    """

    return conn.execute(query, params).fetchall()


# =====================================================
# 5. HOME PAGE
# =====================================================

@app.route("/")
def home():
    return render_template("index.html")


# =====================================================
# 6. LOGIN
# =====================================================

@app.route("/login/<role>", methods=["GET", "POST"])
def login(role):

    if role not in ["admin", "faculty"]:
        return redirect(url_for("home"))

    if request.method == "POST":

        username = request.form.get(
            "username", ""
        ).strip()

        password = request.form.get("password", "")

        if role == "admin":

            if (
                username == ADMIN_USERNAME
                and password == ADMIN_PASSWORD
            ):

                session.clear()
                session["role"] = "admin"
                session["username"] = username

                flash("Admin login successful.", "success")

                return redirect(
                    url_for("admin_dashboard")
                )

            flash("Invalid admin username or password.", "error")

        else:

            conn = get_db_connection()

            try:
                faculty = conn.execute("""
                    SELECT *
                    FROM faculty
                    WHERE username = ?
                """, (username,)).fetchone()

            finally:
                conn.close()

            if faculty:

                stored_hash = faculty["password_hash"]

                if (
                    stored_hash
                    and check_password_hash(
                        stored_hash, password
                    )
                ):

                    session.clear()
                    session["role"] = "faculty"
                    session["faculty_id"] = faculty["id"]
                    session["faculty_name"] = faculty["name"]

                    flash("Faculty login successful.", "success")

                    return redirect(
                        url_for("faculty_dashboard")
                    )

            flash("Invalid faculty username or password.", "error")

    return render_template("login.html", role=role)


# =====================================================
# 7. ADMIN DASHBOARD
# =====================================================

@app.route("/admin-dashboard")
def admin_dashboard():

    if not admin_required():
        return redirect(url_for("login", role="admin"))

    conn = get_db_connection()

    try:

        faculty_count = conn.execute(
            "SELECT COUNT(*) FROM faculty"
        ).fetchone()[0]

        timetable_count = conn.execute(
            "SELECT COUNT(*) FROM timetable"
        ).fetchone()[0]

        hall_count = conn.execute(
            "SELECT COUNT(*) FROM halls"
        ).fetchone()[0]

        duty_count = conn.execute(
            "SELECT COUNT(*) FROM duty_allocations"
        ).fetchone()[0]

        examination_count = conn.execute(
            "SELECT COUNT(*) FROM examinations"
        ).fetchone()[0]

        recent_faculty = conn.execute("""
            SELECT id, name, department, email, phone, username
            FROM faculty
            ORDER BY id DESC
            LIMIT 5
        """).fetchall()

        return render_template(
            "admin_dashboard.html",
            faculty_count=faculty_count,
            timetable_count=timetable_count,
            hall_count=hall_count,
            duty_count=duty_count,
            examination_count=examination_count,
            total_faculty=faculty_count,
            total_timetable=timetable_count,
            total_halls=hall_count,
            total_duties=duty_count,
            total_examinations=examination_count,
            recent_faculty=recent_faculty
        )

    finally:
        conn.close()


# =====================================================
# 8. FACULTY MANAGEMENT
# =====================================================

@app.route("/faculty-management")
def faculty_management():

    if not admin_required():
        return redirect(url_for("login", role="admin"))

    conn = get_db_connection()

    try:

        faculty_list = conn.execute("""
            SELECT id, name, department, email, phone, username
            FROM faculty
            ORDER BY id DESC
        """).fetchall()

        return render_template(
            "faculty_management.html",
            faculty_list=faculty_list
        )

    finally:
        conn.close()


# =====================================================
# 9. ADD FACULTY
# =====================================================

@app.route("/add-faculty", methods=["GET", "POST"])
def add_faculty():

    if not admin_required():
        return redirect(url_for("login", role="admin"))

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        department = request.form.get("department", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not all([
            name, department, email,
            phone, username, password
        ]):
            flash("Please fill in all fields.", "error")
            return redirect(url_for("add_faculty"))

        if len(password) < 6:
            flash(
                "Password must contain at least 6 characters.",
                "error"
            )
            return redirect(url_for("add_faculty"))

        conn = get_db_connection()

        try:

            password_hash = generate_password_hash(password)

            conn.execute("""
                INSERT INTO faculty (
                    name, department, email,
                    phone, username, password_hash
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                name, department, email,
                phone, username, password_hash
            ))

            conn.commit()

            flash("Faculty added successfully.", "success")

            return redirect(
                url_for("faculty_management")
            )

        except sqlite3.IntegrityError:

            flash(
                "This username already exists. Use another username.",
                "error"
            )

        finally:
            conn.close()

        return redirect(url_for("add_faculty"))

    return render_template("add_faculty.html")


# =====================================================
# 10. TIMETABLE MANAGEMENT
# =====================================================

@app.route("/timetable-management", methods=["GET", "POST"])
def timetable_management():

    if not admin_required():
        return redirect(url_for("login", role="admin"))

    conn = get_db_connection()

    try:

        if request.method == "POST":

            faculty_id = request.form.get(
                "faculty_id", ""
            ).strip()

            day = request.form.get("day", "").strip()
            subject = request.form.get("subject", "").strip()
            semester = request.form.get("semester", "").strip()

            start_time = convert_to_24_hour(
                request.form.get("start_time", "")
            )

            end_time = convert_to_24_hour(
                request.form.get("end_time", "")
            )

            if not all([
                faculty_id, day, subject,
                start_time, end_time
            ]):

                flash("Please fill in all timetable fields.", "error")

            elif not faculty_id.isdigit():

                flash("Invalid faculty selection.", "error")

            elif day not in DAYS:

                flash("Please select a valid day.", "error")

            elif not valid_time_range(start_time, end_time):

                flash(
                    "End time must be later than start time.",
                    "error"
                )

            else:

                faculty = conn.execute("""
                    SELECT id FROM faculty WHERE id = ?
                """, (int(faculty_id),)).fetchone()

                if not faculty:

                    flash("Selected faculty does not exist.", "error")

                else:

                    entries = conn.execute("""
                        SELECT start_time, end_time
                        FROM timetable
                        WHERE faculty_id = ? AND day = ?
                    """, (int(faculty_id), day)).fetchall()

                    clash = any(
                        has_time_overlap(
                            row["start_time"],
                            row["end_time"],
                            start_time,
                            end_time
                        )
                        for row in entries
                    )

                    if clash:

                        flash(
                            "This faculty member already has a class during that time.",
                            "error"
                        )

                    else:

                        conn.execute("""
                            INSERT INTO timetable (
                                faculty_id, day, subject,
                                start_time, end_time, semester
                            )
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            int(faculty_id), day, subject,
                            start_time, end_time, semester
                        ))

                        conn.commit()

                        flash(
                            "Timetable entry added successfully.",
                            "success"
                        )

                        return redirect(
                            url_for("timetable_management")
                        )

        faculty_list = conn.execute("""
            SELECT id, name, department
            FROM faculty
            ORDER BY name
        """).fetchall()

        timetable_list = conn.execute("""
            SELECT
                t.*,
                f.name AS faculty_name,
                f.name AS name,
                f.department
            FROM timetable t
            JOIN faculty f ON t.faculty_id = f.id
            ORDER BY
                CASE t.day
                    WHEN 'Monday' THEN 1
                    WHEN 'Tuesday' THEN 2
                    WHEN 'Wednesday' THEN 3
                    WHEN 'Thursday' THEN 4
                    WHEN 'Friday' THEN 5
                    WHEN 'Saturday' THEN 6
                    ELSE 7
                END,
                t.start_time
        """).fetchall()

        return render_template(
            "timetable_management.html",
            faculty_list=faculty_list,
            timetable_list=timetable_list,
            timetable=timetable_list,
            days=DAYS
        )

    finally:
        conn.close()


# =====================================================
# 11. EXAMINATION MANAGEMENT
# =====================================================

@app.route("/examination-management", methods=["GET", "POST"])
def examination_management():

    if not admin_required():
        return redirect(url_for("login", role="admin"))

    conn = get_db_connection()

    try:

        if request.method == "POST":

            exam_name = request.form.get(
                "exam_name", ""
            ).strip()

            subject = request.form.get(
                "subject", ""
            ).strip()

            department = request.form.get(
                "department", ""
            ).strip()

            semester = request.form.get(
                "semester", ""
            ).strip()

            exam_date = request.form.get(
                "exam_date", ""
            ).strip()

            start_time = convert_to_24_hour(
                request.form.get("start_time", "")
            )

            end_time = convert_to_24_hour(
                request.form.get("end_time", "")
            )

            student_count_value = request.form.get(
                "student_count", "0"
            ).strip()

            if not all([
                exam_name, subject, department,
                exam_date, start_time, end_time
            ]):

                flash("Please fill in all required fields.", "error")

            elif not valid_date(exam_date):

                flash("Please enter a valid examination date.", "error")

            elif not valid_time_range(start_time, end_time):

                flash(
                    "End time must be later than start time.",
                    "error"
                )

            else:

                try:

                    student_count = int(student_count_value)

                    if student_count < 0:
                        raise ValueError

                except (ValueError, TypeError):

                    student_count = None

                    flash(
                        "Student count must be a non-negative number.",
                        "error"
                    )

                if student_count is not None:

                    conn.execute("""
                        INSERT INTO examinations (
                            exam_name, subject, department,
                            semester, exam_date, start_time,
                            end_time, student_count
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        exam_name, subject, department,
                        semester, exam_date, start_time,
                        end_time, student_count
                    ))

                    conn.commit()

                    flash(
                        "Examination added successfully.",
                        "success"
                    )

                    return redirect(
                        url_for("examination_management")
                    )

        exam_list = conn.execute("""
            SELECT *
            FROM examinations
            ORDER BY exam_date DESC, start_time
        """).fetchall()

        return render_template(
            "examination_management.html",
            exam_list=exam_list,
            examinations=exam_list
        )

    finally:
        conn.close()


# =====================================================
# 12. HALL MANAGEMENT
# =====================================================

@app.route("/hall-management", methods=["GET", "POST"])
def hall_management():

    if not admin_required():
        return redirect(url_for("login", role="admin"))

    conn = get_db_connection()

    try:

        if request.method == "POST":

            hall_number = request.form.get(
                "hall_number", ""
            ).strip()

            building_name = request.form.get(
                "building_name", ""
            ).strip()

            capacity_value = request.form.get(
                "seating_capacity", ""
            ).strip()

            if not all([
                hall_number, building_name, capacity_value
            ]):

                flash("Please fill in all hall details.", "error")

            else:

                try:

                    capacity = int(capacity_value)

                    if capacity <= 0:
                        raise ValueError

                except ValueError:

                    capacity = None

                    flash(
                        "Seating capacity must be a positive number.",
                        "error"
                    )

                if capacity is not None:

                    try:

                        conn.execute("""
                            INSERT INTO halls (
                                hall_number,
                                building_name,
                                seating_capacity
                            )
                            VALUES (?, ?, ?)
                        """, (
                            hall_number,
                            building_name,
                            capacity
                        ))

                        conn.commit()

                        flash("Hall added successfully.", "success")

                        return redirect(
                            url_for("hall_management")
                        )

                    except sqlite3.IntegrityError:

                        flash(
                            "This hall number already exists.",
                            "error"
                        )

        hall_list = conn.execute("""
            SELECT *
            FROM halls
            ORDER BY hall_number
        """).fetchall()

        return render_template(
            "hall_management.html",
            hall_list=hall_list,
            halls=hall_list
        )

    finally:
        conn.close()


# =====================================================
# 13. DUTY ALLOCATION
# =====================================================

@app.route("/duty-allocation", methods=["GET", "POST"])
def duty_allocation():

    if not admin_required():
        return redirect(url_for("login", role="admin"))

    conn = get_db_connection()

    try:

        if request.method == "POST":

            exam_id_value = request.form.get(
                "exam_id", ""
            ).strip()

            hall_id_value = request.form.get(
                "hall_id", ""
            ).strip()

            faculty_id_value = request.form.get(
                "faculty_id", ""
            ).strip()

            if not all([
                exam_id_value, hall_id_value, faculty_id_value
            ]):

                flash(
                    "Please select examination, hall and faculty.",
                    "error"
                )

            elif not all([
                exam_id_value.isdigit(),
                hall_id_value.isdigit(),
                faculty_id_value.isdigit()
            ]):

                flash("Invalid selection. Please try again.", "error")

            else:

                exam_id = int(exam_id_value)
                hall_id = int(hall_id_value)
                faculty_id = int(faculty_id_value)

                exam = conn.execute("""
                    SELECT *
                    FROM examinations
                    WHERE id = ?
                """, (exam_id,)).fetchone()

                hall = conn.execute("""
                    SELECT *
                    FROM halls
                    WHERE id = ?
                """, (hall_id,)).fetchone()

                faculty = conn.execute("""
                    SELECT *
                    FROM faculty
                    WHERE id = ?
                """, (faculty_id,)).fetchone()

                if not exam:

                    flash("Selected examination does not exist.", "error")

                elif not hall:

                    flash("Selected hall does not exist.", "error")

                elif not faculty:

                    flash("Selected faculty does not exist.", "error")

                elif not valid_date(exam["exam_date"]):

                    flash("The examination date is invalid.", "error")

                elif not valid_time_range(
                    exam["start_time"],
                    exam["end_time"]
                ):

                    flash("The examination time is invalid.", "error")

                elif int(exam["student_count"] or 0) > int(
                    hall["seating_capacity"]
                ):

                    flash(
                        "The selected hall does not have enough seating capacity.",
                        "error"
                    )

                else:

                    duty_date = exam["exam_date"]
                    start_time = exam["start_time"]
                    end_time = exam["end_time"]

                    hall_entries = conn.execute("""
                        SELECT start_time, end_time
                        FROM duty_allocations
                        WHERE hall_id = ? AND duty_date = ?
                    """, (hall_id, duty_date)).fetchall()

                    hall_clash = any(
                        has_time_overlap(
                            row["start_time"],
                            row["end_time"],
                            start_time,
                            end_time
                        )
                        for row in hall_entries
                    )

                    faculty_entries = conn.execute("""
                        SELECT start_time, end_time
                        FROM duty_allocations
                        WHERE faculty_id = ? AND duty_date = ?
                    """, (faculty_id, duty_date)).fetchall()

                    faculty_clash = any(
                        has_time_overlap(
                            row["start_time"],
                            row["end_time"],
                            start_time,
                            end_time
                        )
                        for row in faculty_entries
                    )

                    exam_weekday = datetime.strptime(
                        duty_date, "%Y-%m-%d"
                    ).strftime("%A")

                    timetable_entries = conn.execute("""
                        SELECT start_time, end_time
                        FROM timetable
                        WHERE faculty_id = ? AND day = ?
                    """, (
                        faculty_id,
                        exam_weekday
                    )).fetchall()

                    timetable_clash = any(
                        has_time_overlap(
                            row["start_time"],
                            row["end_time"],
                            start_time,
                            end_time
                        )
                        for row in timetable_entries
                    )

                    if hall_clash:

                        flash(
                            "This hall is already allocated during this time.",
                            "error"
                        )

                    elif faculty_clash:

                        flash(
                            "This faculty member already has another duty during this time.",
                            "error"
                        )

                    elif timetable_clash:

                        flash(
                            "This faculty member has a scheduled class during the examination.",
                            "error"
                        )

                    else:

                        try:

                            conn.execute("""
                                INSERT INTO duty_allocations (
                                    exam_id, hall_id, faculty_id,
                                    duty_date, start_time, end_time
                                )
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (
                                exam_id, hall_id, faculty_id,
                                duty_date, start_time, end_time
                            ))

                            notification_message = (
                                f"You have been assigned examination duty "
                                f"for {exam['exam_name']} "
                                f"({exam['subject']}) in Hall "
                                f"{hall['hall_number']}, "
                                f"{hall['building_name']} on {duty_date} "
                                f"from {convert_to_12_hour(start_time)} "
                                f"to {convert_to_12_hour(end_time)}."
                            )

                            conn.execute("""
                                INSERT INTO notifications (
                                    faculty_id, message,
                                    created_at, is_read
                                )
                                VALUES (?, ?, ?, 0)
                            """, (
                                faculty_id,
                                notification_message,
                                datetime.now().strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                )
                            ))

                            conn.commit()

                            flash(
                                "Duty allocated successfully and notification created.",
                                "success"
                            )

                            return redirect(
                                url_for("duty_allocation")
                            )

                        except sqlite3.Error:

                            conn.rollback()

                            flash(
                                "Unable to allocate duty. Please try again.",
                                "error"
                            )

        exam_list = conn.execute("""
            SELECT *
            FROM examinations
            ORDER BY exam_date DESC
        """).fetchall()

        hall_list = conn.execute("""
            SELECT *
            FROM halls
            ORDER BY hall_number
        """).fetchall()

        faculty_list = conn.execute("""
            SELECT id, name, department
            FROM faculty
            ORDER BY name
        """).fetchall()

        duty_list = conn.execute("""
            SELECT
                d.*,
                e.exam_name,
                e.subject,
                f.name AS faculty_name,
                h.hall_number,
                h.building_name
            FROM duty_allocations d
            JOIN examinations e ON d.exam_id = e.id
            JOIN faculty f ON d.faculty_id = f.id
            JOIN halls h ON d.hall_id = h.id
            ORDER BY d.duty_date DESC, d.start_time
        """).fetchall()

        return render_template(
            "duty_allocation.html",
            exam_list=exam_list,
            hall_list=hall_list,
            faculty_list=faculty_list,
            duty_list=duty_list
        )

    finally:
        conn.close()


# =====================================================
# 14. FACULTY DASHBOARD
# =====================================================

@app.route("/faculty-dashboard")
def faculty_dashboard():

    if not faculty_required():
        return redirect(url_for("login", role="faculty"))

    faculty_id = session.get("faculty_id")

    conn = get_db_connection()

    try:

        faculty = conn.execute("""
            SELECT *
            FROM faculty
            WHERE id = ?
        """, (faculty_id,)).fetchone()

        if not faculty:

            session.clear()

            flash(
                "Faculty account not found. Please login again.",
                "error"
            )

            return redirect(
                url_for("login", role="faculty")
            )

        timetable_list = conn.execute("""
            SELECT *
            FROM timetable
            WHERE faculty_id = ?
            ORDER BY
                CASE day
                    WHEN 'Monday' THEN 1
                    WHEN 'Tuesday' THEN 2
                    WHEN 'Wednesday' THEN 3
                    WHEN 'Thursday' THEN 4
                    WHEN 'Friday' THEN 5
                    WHEN 'Saturday' THEN 6
                    ELSE 7
                END,
                start_time
        """, (faculty_id,)).fetchall()

        duties = conn.execute("""
            SELECT
                d.id,
                d.duty_date,
                d.start_time,
                d.end_time,
                e.exam_name,
                e.subject,
                e.department,
                e.semester,
                h.hall_number,
                h.building_name
            FROM duty_allocations d
            JOIN examinations e ON d.exam_id = e.id
            JOIN halls h ON d.hall_id = h.id
            WHERE d.faculty_id = ?
            ORDER BY d.duty_date DESC, d.start_time
        """, (faculty_id,)).fetchall()

        today = datetime.now().strftime("%Y-%m-%d")

        upcoming_duty_count = conn.execute("""
            SELECT COUNT(*)
            FROM duty_allocations
            WHERE faculty_id = ?
            AND duty_date >= ?
        """, (faculty_id, today)).fetchone()[0]

        notifications = conn.execute("""
            SELECT *
            FROM notifications
            WHERE faculty_id = ?
            ORDER BY id DESC
        """, (faculty_id,)).fetchall()

        unread_notification_count = conn.execute("""
            SELECT COUNT(*)
            FROM notifications
            WHERE faculty_id = ?
            AND is_read = 0
        """, (faculty_id,)).fetchone()[0]

        return render_template(
            "faculty_dashboard.html",
            faculty=faculty,
            timetable=timetable_list,
            timetable_list=timetable_list,
            duties=duties,
            duty_list=duties,
            duty_count=len(duties),
            upcoming_duty_count=upcoming_duty_count,
            notifications=notifications,
            unread_notification_count=unread_notification_count
        )

    finally:
        conn.close()


# =====================================================
# 15. MARK NOTIFICATION AS READ
# =====================================================

@app.route(
    "/mark-notification-read/<int:notification_id>",
    methods=["POST"]
)
def mark_notification_read(notification_id):

    if not faculty_required():
        return redirect(url_for("login", role="faculty"))

    faculty_id = session.get("faculty_id")

    conn = get_db_connection()

    try:

        conn.execute("""
            UPDATE notifications
            SET is_read = 1
            WHERE id = ?
            AND faculty_id = ?
        """, (notification_id, faculty_id))

        conn.commit()

        flash("Notification marked as read.", "success")

        return redirect(
            url_for("faculty_dashboard")
        )

    finally:
        conn.close()


# =====================================================
# 16. REPORTS
# =====================================================

@app.route("/reports", methods=["GET", "POST"])
def reports():

    if not admin_required():
        return redirect(url_for("login", role="admin"))

    selected_exam = request.values.get(
        "exam_id", ""
    ).strip()

    selected_faculty = request.values.get(
        "faculty_id", ""
    ).strip()

    selected_date = request.values.get(
        "duty_date",
        request.values.get("date", "")
    ).strip()

    conn = get_db_connection()

    try:

        exam_list = conn.execute("""
            SELECT id, exam_name, exam_date
            FROM examinations
            ORDER BY exam_date DESC
        """).fetchall()

        faculty_list = conn.execute("""
            SELECT id, name, department
            FROM faculty
            ORDER BY name
        """).fetchall()

        if selected_exam and not selected_exam.isdigit():
            selected_exam = ""

        if selected_faculty and not selected_faculty.isdigit():
            selected_faculty = ""

        if selected_date and not valid_date(selected_date):
            selected_date = ""

        report_list = get_filtered_report_data(
            conn,
            exam_id=int(selected_exam) if selected_exam else None,
            faculty_id=int(selected_faculty) if selected_faculty else None,
            duty_date=selected_date if selected_date else None
        )

        total_duties = len(report_list)

        total_faculty = len({
            row["faculty_id"] for row in report_list
        })

        total_examinations = len({
            row["exam_id"] for row in report_list
        })

        return render_template(
            "reports.html",
            report_list=report_list,
            exam_list=exam_list,
            faculty_list=faculty_list,
            selected_exam=selected_exam,
            selected_faculty=selected_faculty,
            selected_date=selected_date,
            total_duties=total_duties,
            total_faculty=total_faculty,
            total_examinations=total_examinations
        )

    finally:
        conn.close()


# =====================================================
# 17. EXPORT REPORTS TO CSV
# =====================================================

@app.route("/reports/export-csv")
def export_reports_csv():

    if not admin_required():
        return redirect(url_for("login", role="admin"))

    selected_exam = request.args.get(
        "exam_id", ""
    ).strip()

    selected_faculty = request.args.get(
        "faculty_id", ""
    ).strip()

    selected_date = request.args.get(
        "duty_date",
        request.args.get("date", "")
    ).strip()

    if selected_exam and not selected_exam.isdigit():
        selected_exam = ""

    if selected_faculty and not selected_faculty.isdigit():
        selected_faculty = ""

    if selected_date and not valid_date(selected_date):
        selected_date = ""

    conn = get_db_connection()

    try:

        report_list = get_filtered_report_data(
            conn,
            exam_id=int(selected_exam) if selected_exam else None,
            faculty_id=int(selected_faculty) if selected_faculty else None,
            duty_date=selected_date if selected_date else None
        )

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "Examination Name",
            "Subject",
            "Department",
            "Semester",
            "Faculty Name",
            "Faculty Department",
            "Hall Number",
            "Building Name",
            "Duty Date",
            "Start Time",
            "End Time"
        ])

        for row in report_list:

            writer.writerow([
                row["exam_name"],
                row["subject"],
                row["department"],
                row["semester"],
                row["faculty_name"],
                row["faculty_department"],
                row["hall_number"],
                row["building_name"],
                row["duty_date"],
                convert_to_12_hour(row["start_time"]),
                convert_to_12_hour(row["end_time"])
            ])

        response = make_response(
            "\ufeff" + output.getvalue()
        )

        response.headers["Content-Disposition"] = (
            "attachment; filename=examination_duty_report.csv"
        )

        response.headers["Content-Type"] = (
            "text/csv; charset=utf-8"
        )

        return response

    finally:
        conn.close()


# =====================================================
# 18. LOGOUT
# =====================================================

@app.route("/logout")
def logout():

    session.clear()

    flash("You have been logged out successfully.", "success")

    return redirect(url_for("home"))


# =====================================================
# 19. START APPLICATION
# =====================================================

if __name__ == "__main__":

    app.run(
        debug=True,
        use_reloader=False
    )
