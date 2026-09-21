import os
import uuid
import sqlite3
from functools import wraps
from urllib.parse import quote

import requests

from flask import (
    Flask,
    request,
    jsonify,
    session,
    render_template,
    send_file
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)


# ============================================================
# APP CONFIGURATION
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "my-ai-studio-local-secret-2026"
)

app.config["SESSION_COOKIE_NAME"] = (
    "my_ai_studio_session"
)

app.config["SESSION_COOKIE_HTTPONLY"] = True

app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

app.config["SESSION_COOKIE_SECURE"] = False

app.config["PERMANENT_SESSION_LIFETIME"] = (
    60 * 60 * 24 * 30
)


# ============================================================
# CREDIT CONFIGURATION
# ============================================================

NEW_USER_CREDITS = 100

IMAGE_COST = 5

AUDIO_COST = 8

VIDEO_COST = 21


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    ""
)

USE_POSTGRES = bool(
    DATABASE_URL
)


try:

    import psycopg2

    from psycopg2.extras import RealDictCursor

except ImportError:

    psycopg2 = None

    RealDictCursor = None


# ============================================================
# POLLINATIONS CONFIGURATION
# ============================================================

POLLINATIONS_API_KEY = os.environ.get(
    "POLLINATIONS_API_KEY",
    ""
)

POLLINATIONS_BASE = (
    "https://gen.pollinations.ai"
)


# ============================================================
# ADMIN CONFIGURATION
# ============================================================

ADMIN_USERNAME = os.environ.get(
    "ADMIN_USERNAME",
    "CHANDANADMIN"
)

ADMIN_PASSWORD = os.environ.get(
    "ADMIN_PASSWORD",
    ""
)


# ============================================================
# GENERATED FILES DIRECTORY
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

GENERATED_DIR = os.path.join(
    BASE_DIR,
    "generated"
)

os.makedirs(
    GENERATED_DIR,
    exist_ok=True
)


# ============================================================
# DATABASE HELPERS
# ============================================================

def db_placeholder():

    if USE_POSTGRES:

        return "%s"

    return "?"


def db_connection():

    if USE_POSTGRES:

        if not psycopg2:

            raise RuntimeError(
                "psycopg2 is not installed."
            )

        return psycopg2.connect(
            DATABASE_URL
        )

    connection = sqlite3.connect(
        os.path.join(
            BASE_DIR,
            "users.db"
        )
    )

    connection.row_factory = sqlite3.Row

    return connection


def db_bool(value):

    if USE_POSTGRES:

        return bool(value)

    return 1 if value else 0


def row_value(
    row,
    key,
    index=None,
    default=None
):

    if row is None:

        return default

    if isinstance(row, dict):

        return row.get(
            key,
            default
        )

    try:

        return row[key]

    except Exception:

        pass

    if index is not None:

        try:

            return row[index]

        except Exception:

            pass

    return default


def db_execute(
    query,
    params=None,
    fetchone=False,
    fetchall=False,
    commit=False
):

    connection = db_connection()

    try:

        if USE_POSTGRES:

            cursor = connection.cursor(
                cursor_factory=RealDictCursor
            )

        else:

            cursor = connection.cursor()

        cursor.execute(
            query,
            params or ()
        )

        result = None

        if fetchone:

            result = cursor.fetchone()

        elif fetchall:

            result = cursor.fetchall()

        if commit:

            connection.commit()

        return result

    finally:

        try:

            cursor.close()

        except Exception:

            pass

        connection.close()


# ============================================================
# DATABASE COLUMN HELPERS
# ============================================================

def get_columns(table_name):

    connection = db_connection()

    try:

        cursor = connection.cursor()

        if USE_POSTGRES:

            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = %s
                """,
                (table_name,)
            )

            rows = cursor.fetchall()

            return {
                row[0]
                for row in rows
            }

        cursor.execute(
            "PRAGMA table_info(" +
            table_name +
            ")"
        )

        rows = cursor.fetchall()

        return {
            row[1]
            for row in rows
        }

    finally:

        try:

            cursor.close()

        except Exception:

            pass

        connection.close()


def add_column_if_missing(
    table_name,
    column_name,
    column_definition
):

    columns = get_columns(
        table_name
    )

    if column_name in columns:

        return

    connection = db_connection()

    try:

        cursor = connection.cursor()

        sql = (
            "ALTER TABLE "
            + table_name
            + " ADD COLUMN "
            + column_name
            + " "
            + column_definition
        )

        cursor.execute(sql)

        connection.commit()

        print(
            "Added missing column:",
            table_name,
            column_name
        )

    finally:

        try:

            cursor.close()

        except Exception:

            pass

        connection.close()


# ============================================================
# CREATE DATABASE TABLES
# ============================================================

def create_tables():

    connection = db_connection()

    try:

        cursor = connection.cursor()

        if USE_POSTGRES:

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    credits INTEGER NOT NULL DEFAULT 100,
                    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                    blocked BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_history (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    credits INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS credit_history (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

        else:

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    credits INTEGER NOT NULL DEFAULT 100,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    blocked INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    credits INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS credit_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

        connection.commit()

    finally:

        try:

            cursor.close()

        except Exception:

            pass

        connection.close()


# ============================================================
# DATABASE MIGRATION
# ============================================================

def migrate_database():

    print(
        "Starting database migration..."
    )

    create_tables()


    # ========================================================
    # USERS TABLE
    # ========================================================

    if USE_POSTGRES:

        add_column_if_missing(
            "users",
            "is_admin",
            "BOOLEAN NOT NULL DEFAULT FALSE"
        )

        add_column_if_missing(
            "users",
            "blocked",
            "BOOLEAN NOT NULL DEFAULT FALSE"
        )

        add_column_if_missing(
            "users",
            "created_at",
            "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )

    else:

        add_column_if_missing(
            "users",
            "is_admin",
            "INTEGER NOT NULL DEFAULT 0"
        )

        add_column_if_missing(
            "users",
            "blocked",
            "INTEGER NOT NULL DEFAULT 0"
        )

        add_column_if_missing(
            "users",
            "created_at",
            "TIMESTAMP"
        )


    # ========================================================
    # GENERATION HISTORY
    # ========================================================

    add_column_if_missing(
        "generation_history",
        "type",
        "TEXT NOT NULL DEFAULT 'unknown'"
    )

    add_column_if_missing(
        "generation_history",
        "prompt",
        "TEXT NOT NULL DEFAULT ''"
    )

    add_column_if_missing(
        "generation_history",
        "credits",
        "INTEGER NOT NULL DEFAULT 0"
    )

    if USE_POSTGRES:

        add_column_if_missing(
            "generation_history",
            "created_at",
            "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )

    else:

        add_column_if_missing(
            "generation_history",
            "created_at",
            "TIMESTAMP"
        )


    # ========================================================
    # CREDIT HISTORY
    # ========================================================

    add_column_if_missing(
        "credit_history",
        "amount",
        "INTEGER NOT NULL DEFAULT 0"
    )

    add_column_if_missing(
        "credit_history",
        "reason",
        "TEXT NOT NULL DEFAULT ''"
    )

    if USE_POSTGRES:

        add_column_if_missing(
            "credit_history",
            "created_at",
            "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )

    else:

        add_column_if_missing(
            "credit_history",
            "created_at",
            "TIMESTAMP"
        )


    print(
        "Database migration completed."
    )


# ============================================================
# RUN DATABASE MIGRATION
# ============================================================

try:

    migrate_database()

except Exception as e:

    print(
        "DATABASE MIGRATION ERROR:",
        repr(e)
    )

    raise
# ============================================================
# USER HELPERS
# ============================================================

def get_user_by_id(user_id):

    placeholder = db_placeholder()

    return db_execute(
        f"""
        SELECT
            id,
            username,
            password,
            credits,
            is_admin,
            blocked,
            created_at
        FROM users
        WHERE id = {placeholder}
        """,
        (user_id,),
        fetchone=True
    )


def get_user_by_username(username):

    placeholder = db_placeholder()

    return db_execute(
        f"""
        SELECT
            id,
            username,
            password,
            credits,
            is_admin,
            blocked,
            created_at
        FROM users
        WHERE username = {placeholder}
        """,
        (username,),
        fetchone=True
    )


def get_credits(user_id):

    user = get_user_by_id(
        user_id
    )

    if not user:

        return 0

    return int(
        row_value(
            user,
            "credits",
            3,
            0
        )
        or 0
    )


# ============================================================
# AUTH DECORATORS
# ============================================================

def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        user_id = session.get(
            "user_id"
        )

        if not user_id:

            return jsonify(
                {
                    "error":
                        "Login required."
                }
            ), 401

        user = get_user_by_id(
            user_id
        )

        if not user:

            session.clear()

            return jsonify(
                {
                    "error":
                        "User session expired."
                }
            ), 401

        blocked = row_value(
            user,
            "blocked",
            5,
            False
        )

        if blocked:

            session.clear()

            return jsonify(
                {
                    "error":
                        "Your account is blocked."
                }
            ), 403

        return function(
            *args,
            **kwargs
        )

    return wrapper


def admin_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        user_id = session.get(
            "user_id"
        )

        if not user_id:

            return jsonify(
                {
                    "error":
                        "Login required."
                }
            ), 401

        user = get_user_by_id(
            user_id
        )

        if not user:

            session.clear()

            return jsonify(
                {
                    "error":
                        "User session expired."
                }
            ), 401

        blocked = row_value(
            user,
            "blocked",
            5,
            False
        )

        is_admin = row_value(
            user,
            "is_admin",
            4,
            False
        )

        if blocked:

            session.clear()

            return jsonify(
                {
                    "error":
                        "Your account is blocked."
                }
            ), 403

        if not is_admin:

            return jsonify(
                {
                    "error":
                        "Admin access required."
                }
            ), 403

        return function(
            *args,
            **kwargs
        )

    return wrapper


# ============================================================
# ADMIN BOOTSTRAP
# ============================================================

def make_configured_admin():

    if not ADMIN_USERNAME:

        return

    user = get_user_by_username(
        ADMIN_USERNAME
    )

    if not user:

        return

    placeholder = db_placeholder()

    db_execute(
        f"""
        UPDATE users
        SET
            is_admin = {db_placeholder()},
            blocked = {db_placeholder()}
        WHERE username = {placeholder}
        """,
        (
            db_bool(True),
            db_bool(False),
            ADMIN_USERNAME
        ),
        commit=True
    )


try:

    make_configured_admin()

except Exception as e:

    print(
        "ADMIN BOOTSTRAP ERROR:",
        repr(e)
    )


# ============================================================
# CREDIT HELPERS
# ============================================================

def add_credits(
    user_id,
    amount,
    reason
):

    if amount <= 0:

        return False

    placeholder = db_placeholder()

    connection = db_connection()

    try:

        cursor = connection.cursor()

        cursor.execute(
            f"""
            UPDATE users
            SET credits = credits + {placeholder}
            WHERE id = {placeholder}
            """,
            (
                amount,
                user_id
            )
        )

        cursor.execute(
            f"""
            INSERT INTO credit_history
            (
                user_id,
                amount,
                reason
            )
            VALUES
            (
                {placeholder},
                {placeholder},
                {placeholder}
            )
            """,
            (
                user_id,
                amount,
                reason
            )
        )

        connection.commit()

        return True

    except Exception:

        connection.rollback()

        raise

    finally:

        try:

            cursor.close()

        except Exception:

            pass

        connection.close()


def use_credits(
    user_id,
    amount,
    reason
):

    if amount <= 0:

        return False

    placeholder = db_placeholder()

    connection = db_connection()

    try:

        cursor = connection.cursor()

        cursor.execute(
            f"""
            UPDATE users
            SET credits = credits - {placeholder}
            WHERE id = {placeholder}
              AND credits >= {placeholder}
            """,
            (
                amount,
                user_id,
                amount
            )
        )

        if cursor.rowcount != 1:

            connection.rollback()

            return False

        cursor.execute(
            f"""
            INSERT INTO credit_history
            (
                user_id,
                amount,
                reason
            )
            VALUES
            (
                {placeholder},
                {placeholder},
                {placeholder}
            )
            """,
            (
                user_id,
                -amount,
                reason
            )
        )

        connection.commit()

        return True

    except Exception:

        connection.rollback()

        raise

    finally:

        try:

            cursor.close()

        except Exception:

            pass

        connection.close()


# ============================================================
# GENERATION HISTORY
# ============================================================

def save_generation(
    user_id,
    generation_type,
    prompt,
    credits
):

    placeholder = db_placeholder()

    db_execute(
        f"""
        INSERT INTO generation_history
        (
            user_id,
            type,
            prompt,
            credits
        )
        VALUES
        (
            {placeholder},
            {placeholder},
            {placeholder},
            {placeholder}
        )
        """,
        (
            user_id,
            generation_type,
            prompt,
            credits
        ),
        commit=True
    )


# ============================================================
# POLLINATIONS HELPERS
# ============================================================

def require_pollinations_key():

    if not POLLINATIONS_API_KEY:

        raise RuntimeError(
            "POLLINATIONS_API_KEY is not configured."
        )


def pollinations_headers():

    require_pollinations_key()

    return {
        "Authorization":
            "Bearer "
            + POLLINATIONS_API_KEY,

        "User-Agent":
            "My-AI-Studio/1.0"
    }


# ============================================================
# BASIC ROUTES
# ============================================================

@app.route("/")
def home():

    return render_template(
        "index.html"
    )


@app.route("/health")
def health():

    return jsonify(
        {
            "status":
                "ok",

            "service":
                "My AI Studio"
        }
    )


# ============================================================
# REGISTER
# ============================================================

@app.route(
    "/register",
    methods=["POST"]
)
def register():

    data = request.get_json(
        silent=True
    ) or {}

    username = str(
        data.get(
            "username",
            ""
        )
    ).strip()

    password = str(
        data.get(
            "password",
            ""
        )
    )

    if not username or not password:

        return jsonify(
            {
                "error":
                    "Username and password are required."
            }
        ), 400

    if len(username) < 3:

        return jsonify(
            {
                "error":
                    "Username must be at least 3 characters."
            }
        ), 400

    if len(password) < 6:

        return jsonify(
            {
                "error":
                    "Password must be at least 6 characters."
            }
        ), 400

    existing = get_user_by_username(
        username
    )

    if existing:

        return jsonify(
            {
                "error":
                    "Username already exists."
            }
        ), 409

    hashed_password = (
        generate_password_hash(
            password
        )
    )

    placeholder = db_placeholder()

    connection = db_connection()

    try:

        cursor = connection.cursor()

        cursor.execute(
            f"""
            INSERT INTO users
            (
                username,
                password,
                credits,
                is_admin,
                blocked
            )
            VALUES
            (
                {placeholder},
                {placeholder},
                {placeholder},
                {placeholder},
                {placeholder}
            )
            """,
            (
                username,
                hashed_password,
                NEW_USER_CREDITS,
                db_bool(False),
                db_bool(False)
            )
        )

        connection.commit()

        user = get_user_by_username(
            username
        )

        user_id = row_value(
            user,
            "id",
            0
        )

        add_credits(
            user_id,
            NEW_USER_CREDITS,
            "New account bonus"
        )

        return jsonify(
            {
                "success":
                    True,

                "message":
                    "Registration successful."
            }
        )

    except Exception as e:

        connection.rollback()

        print(
            "REGISTER ERROR:",
            repr(e)
        )

        return jsonify(
            {
                "error":
                    "Registration failed."
            }
        ), 500

    finally:

        try:

            cursor.close()

        except Exception:

            pass

        connection.close()
        # ============================================================
# LOGIN
# ============================================================

@app.route(
    "/login",
    methods=["POST"]
)
def login():

    data = request.get_json(
        silent=True
    ) or {}

    username = str(
        data.get(
            "username",
            ""
        )
    ).strip()

    password = str(
        data.get(
            "password",
            ""
        )
    )

    if not username or not password:

        return jsonify(
            {
                "error":
                    "Username and password are required."
            }
        ), 400

    user = get_user_by_username(
        username
    )

    if not user:

        return jsonify(
            {
                "error":
                    "Invalid username or password."
            }
        ), 401

    stored_password = row_value(
        user,
        "password",
        2,
        ""
    )

    try:

        valid_password = (
            check_password_hash(
                stored_password,
                password
            )
        )

    except Exception:

        valid_password = False

    if not valid_password:

        return jsonify(
            {
                "error":
                    "Invalid username or password."
            }
        ), 401

    blocked = row_value(
        user,
        "blocked",
        5,
        False
    )

    if blocked:

        return jsonify(
            {
                "error":
                    "Your account is blocked."
            }
        ), 403

    user_id = row_value(
        user,
        "id",
        0
    )

    is_admin = row_value(
        user,
        "is_admin",
        4,
        False
    )

    session.clear()

    session.permanent = True

    session["user_id"] = int(
        user_id
    )

    session["username"] = username

    session["is_admin"] = bool(
        is_admin
    )

    session.modified = True

    return jsonify(
        {
            "success":
                True,

            "logged_in":
                True,

            "username":
                username,

            "credits":
                get_credits(user_id),

            "is_admin":
                bool(is_admin)
        }
    )


# ============================================================
# CURRENT USER
# ============================================================

@app.route("/me")
def me():

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return jsonify(
            {
                "logged_in":
                    False
            }
        )

    user = get_user_by_id(
        user_id
    )

    if not user:

        session.clear()

        return jsonify(
            {
                "logged_in":
                    False
            }
        )

    blocked = row_value(
        user,
        "blocked",
        5,
        False
    )

    if blocked:

        session.clear()

        return jsonify(
            {
                "logged_in":
                    False
            }
        )

    return jsonify(
        {
            "logged_in":
                True,

            "username":
                row_value(
                    user,
                    "username",
                    1,
                    ""
                ),

            "credits":
                row_value(
                    user,
                    "credits",
                    3,
                    0
                ),

            "is_admin":
                bool(
                    row_value(
                        user,
                        "is_admin",
                        4,
                        False
                    )
                )
        }
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route(
    "/logout",
    methods=["POST"]
)
def logout():

    session.clear()

    return jsonify(
        {
            "success":
                True
        }
    )


# ============================================================
# CREDITS
# ============================================================

@app.route("/credits")
@login_required
def credits():

    user_id = session.get(
        "user_id"
    )

    return jsonify(
        {
            "credits":
                get_credits(user_id)
        }
    )


# ============================================================
# IMAGE GENERATION
# ============================================================

@app.route(
    "/generate",
    methods=["POST"]
)
@login_required
def generate_image():

    data = request.get_json(
        silent=True
    ) or {}

    prompt = str(
        data.get(
            "prompt",
            ""
        )
    ).strip()

    if not prompt:

        return jsonify(
            {
                "error":
                    "Prompt is required."
            }
        ), 400

    user_id = session.get(
        "user_id"
    )

    if get_credits(user_id) < IMAGE_COST:

        return jsonify(
            {
                "error":
                    "Not enough credits."
            }
        ), 402

    charged = use_credits(
        user_id,
        IMAGE_COST,
        "Image generation"
    )

    if not charged:

        return jsonify(
            {
                "error":
                    "Not enough credits."
            }
        ), 402

    filename = (
        uuid.uuid4().hex
        + ".png"
    )

    filepath = os.path.join(
        GENERATED_DIR,
        filename
    )

    try:

        encoded_prompt = quote(
            prompt,
            safe=""
        )

        url = (
            POLLINATIONS_BASE
            + "/image/"
            + encoded_prompt
        )

        response = requests.get(
            url,
            headers=pollinations_headers(),
            params={
                "model":
                    "black-forest-labs/flux.1-schnell",

                "width":
                    1024,

                "height":
                    1024,

                "n":
                    1
            },
            timeout=300
        )

        response.raise_for_status()

        with open(
            filepath,
            "wb"
        ) as file:

            file.write(
                response.content
            )

        save_generation(
            user_id,
            "image",
            prompt,
            IMAGE_COST
        )

        return jsonify(
            {
                "success":
                    True,

                "type":
                    "image",

                "credits":
                    get_credits(user_id),

                "url":
                    "/generated/"
                    + filename
            }
        )

    except Exception as e:

        print(
            "IMAGE GENERATION ERROR:",
            repr(e)
        )

        add_credits(
            user_id,
            IMAGE_COST,
            "Image generation refund"
        )

        return jsonify(
            {
                "error":
                    "Image generation failed."
            }
        ), 500


# ============================================================
# AUDIO GENERATION
# ============================================================

@app.route(
    "/audio",
    methods=["POST"]
)
@login_required
def generate_audio():

    data = request.get_json(
        silent=True
    ) or {}

    text = str(
        data.get(
            "text",
            ""
        )
    ).strip()

    if not text:

        return jsonify(
            {
                "error":
                    "Text is required."
            }
        ), 400

    user_id = session.get(
        "user_id"
    )

    if get_credits(user_id) < AUDIO_COST:

        return jsonify(
            {
                "error":
                    "Not enough credits."
            }
        ), 402

    charged = use_credits(
        user_id,
        AUDIO_COST,
        "Audio generation"
    )

    if not charged:

        return jsonify(
            {
                "error":
                    "Not enough credits."
            }
        ), 402

    filename = (
        uuid.uuid4().hex
        + ".mp3"
    )

    filepath = os.path.join(
        GENERATED_DIR,
        filename
    )

    try:

        encoded_text = quote(
            text,
            safe=""
        )

        url = (
            POLLINATIONS_BASE
            + "/audio/"
            + encoded_text
        )

        response = requests.get(
            url,
            headers=pollinations_headers(),
            params={
                "voice":
                    "nova"
            },
            timeout=300
        )

        response.raise_for_status()

        with open(
            filepath,
            "wb"
        ) as file:

            file.write(
                response.content
            )

        save_generation(
            user_id,
            "audio",
            text,
            AUDIO_COST
        )

        return jsonify(
            {
                "success":
                    True,

                "type":
                    "audio",

                "credits":
                    get_credits(user_id),

                "url":
                    "/generated/"
                    + filename
            }
        )

    except Exception as e:

        print(
            "AUDIO GENERATION ERROR:",
            repr(e)
        )

        add_credits(
            user_id,
            AUDIO_COST,
            "Audio generation refund"
        )

        return jsonify(
            {
                "error":
                    "Audio generation failed."
            }
        ), 500


# ============================================================
# VIDEO GENERATION
# ============================================================

@app.route(
    "/video",
    methods=["POST"]
)
@login_required
def generate_video():

    data = request.get_json(
        silent=True
    ) or {}

    prompt = str(
        data.get(
            "prompt",
            ""
        )
    ).strip()

    if not prompt:

        return jsonify(
            {
                "error":
                    "Prompt is required."
            }
        ), 400

    user_id = session.get(
        "user_id"
    )

    if get_credits(user_id) < VIDEO_COST:

        return jsonify(
            {
                "error":
                    "Not enough credits."
            }
        ), 402

    charged = use_credits(
        user_id,
        VIDEO_COST,
        "Video generation"
    )

    if not charged:

        return jsonify(
            {
                "error":
                    "Not enough credits."
            }
        ), 402

    filename = (
        uuid.uuid4().hex
        + ".mp4"
    )

    filepath = os.path.join(
        GENERATED_DIR,
        filename
    )

    try:

        encoded_prompt = quote(
            prompt,
            safe=""
        )

        url = (
            POLLINATIONS_BASE
            + "/video/"
            + encoded_prompt
        )

        response = requests.get(
            url,
            headers=pollinations_headers(),
            params={
                "model":
                    "google/veo-3.1-fast",

                "duration":
                    4,

                "aspectRatio":
                    "16:9",

                "audio":
                    "false"
            },
            timeout=600
        )

        response.raise_for_status()

        with open(
            filepath,
            "wb"
        ) as file:

            file.write(
                response.content
            )

        save_generation(
            user_id,
            "video",
            prompt,
            VIDEO_COST
        )

        return jsonify(
            {
                "success":
                    True,

                "type":
                    "video",

                "credits":
                    get_credits(user_id),

                "url":
                    "/generated/"
                    + filename
            }
        )

    except Exception as e:

        print(
            "VIDEO GENERATION ERROR:",
            repr(e)
        )

        add_credits(
            user_id,
            VIDEO_COST,
            "Video generation refund"
        )

        return jsonify(
            {
                "error":
                    "Video generation failed."
            }
        ), 500
    # ============================================================
# LOGIN
# ============================================================

@app.route(
    "/login",
    methods=["POST"]
)
def login():

    data = request.get_json(
        silent=True
    ) or {}

    username = str(
        data.get(
            "username",
            ""
        )
    ).strip()

    password = str(
        data.get(
            "password",
            ""
        )
    )

    if not username or not password:

        return jsonify(
            {
                "error":
                    "Username and password are required."
            }
        ), 400

    user = get_user_by_username(
        username
    )

    if not user:

        return jsonify(
            {
                "error":
                    "Invalid username or password."
            }
        ), 401

    stored_password = row_value(
        user,
        "password",
        2,
        ""
    )

    try:

        valid_password = (
            check_password_hash(
                stored_password,
                password
            )
        )

    except Exception:

        valid_password = False

    if not valid_password:

        return jsonify(
            {
                "error":
                    "Invalid username or password."
            }
        ), 401

    blocked = row_value(
        user,
        "blocked",
        5,
        False
    )

    if blocked:

        return jsonify(
            {
                "error":
                    "Your account is blocked."
            }
        ), 403

    user_id = row_value(
        user,
        "id",
        0
    )

    is_admin = row_value(
        user,
        "is_admin",
        4,
        False
    )

    session.clear()

    session.permanent = True

    session["user_id"] = int(
        user_id
    )

    session["username"] = username

    session["is_admin"] = bool(
        is_admin
    )

    session.modified = True

    return jsonify(
        {
            "success":
                True,

            "logged_in":
                True,

            "username":
                username,

            "credits":
                get_credits(user_id),

            "is_admin":
                bool(is_admin)
        }
    )


# ============================================================
# CURRENT USER
# ============================================================

@app.route("/me")
def me():

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return jsonify(
            {
                "logged_in":
                    False
            }
        )

    user = get_user_by_id(
        user_id
    )

    if not user:

        session.clear()

        return jsonify(
            {
                "logged_in":
                    False
            }
        )

    blocked = row_value(
        user,
        "blocked",
        5,
        False
    )

    if blocked:

        session.clear()

        return jsonify(
            {
                "logged_in":
                    False
            }
        )

    return jsonify(
        {
            "logged_in":
                True,

            "username":
                row_value(
                    user,
                    "username",
                    1,
                    ""
                ),

            "credits":
                row_value(
                    user,
                    "credits",
                    3,
                    0
                ),

            "is_admin":
                bool(
                    row_value(
                        user,
                        "is_admin",
                        4,
                        False
                    )
                )
        }
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route(
    "/logout",
    methods=["POST"]
)
def logout():

    session.clear()

    return jsonify(
        {
            "success":
                True
        }
    )


# ============================================================
# CREDITS
# ============================================================

@app.route("/credits")
@login_required
def credits():

    user_id = session.get(
        "user_id"
    )

    return jsonify(
        {
            "credits":
                get_credits(user_id)
        }
    )


# ============================================================
# IMAGE GENERATION
# ============================================================

@app.route(
    "/generate",
    methods=["POST"]
)
@login_required
def generate_image():

    data = request.get_json(
        silent=True
    ) or {}

    prompt = str(
        data.get(
            "prompt",
            ""
        )
    ).strip()

    if not prompt:

        return jsonify(
            {
                "error":
                    "Prompt is required."
            }
        ), 400

    user_id = session.get(
        "user_id"
    )

    if get_credits(user_id) < IMAGE_COST:

        return jsonify(
            {
                "error":
                    "Not enough credits."
            }
        ), 402

    charged = use_credits(
        user_id,
        IMAGE_COST,
        "Image generation"
    )

    if not charged:

        return jsonify(
            {
                "error":
                    "Not enough credits."
            }
        ), 402

    filename = (
        uuid.uuid4().hex
        + ".png"
    )

    filepath = os.path.join(
        GENERATED_DIR,
        filename
    )

    try:

        encoded_prompt = quote(
            prompt,
            safe=""
        )

        url = (
            POLLINATIONS_BASE
            + "/image/"
            + encoded_prompt
        )

        response = requests.get(
            url,
            headers=pollinations_headers(),
            params={
                "model":
                    "black-forest-labs/flux.1-schnell",

                "width":
                    1024,

                "height":
                    1024,

                "n":
                    1
            },
            timeout=300
        )

        response.raise_for_status()

        with open(
            filepath,
            "wb"
        ) as file:

            file.write(
                response.content
            )

        save_generation(
            user_id,
            "image",
            prompt,
            IMAGE_COST
        )

        return jsonify(
            {
                "success":
                    True,

                "type":
                    "image",

                "credits":
                    get_credits(user_id),

                "url":
                    "/generated/"
                    + filename
            }
        )

    except Exception as e:

        print(
            "IMAGE GENERATION ERROR:",
            repr(e)
        )

        add_credits(
            user_id,
            IMAGE_COST,
            "Image generation refund"
        )

        return jsonify(
            {
                "error":
                    "Image generation failed."
            }
        ), 500


# ============================================================
# AUDIO GENERATION
# ============================================================

@app.route(
    "/audio",
    methods=["POST"]
)
@login_required
def generate_audio():

    data = request.get_json(
        silent=True
    ) or {}

    text = str(
        data.get(
            "text",
            ""
        )
    ).strip()

    if not text:

        return jsonify(
            {
                "error":
                    "Text is required."
            }
        ), 400

    user_id = session.get(
        "user_id"
    )

    if get_credits(user_id) < AUDIO_COST:

        return jsonify(
            {
                "error":
                    "Not enough credits."
            }
        ), 402

    charged = use_credits(
        user_id,
        AUDIO_COST,
        "Audio generation"
    )

    if not charged:

        return jsonify(
            {
                "error":
                    "Not enough credits."
            }
        ), 402

    filename = (
        uuid.uuid4().hex
        + ".mp3"
    )

    filepath = os.path.join(
        GENERATED_DIR,
        filename
    )

    try:

        encoded_text = quote(
            text,
            safe=""
        )

        url = (
            POLLINATIONS_BASE
            + "/audio/"
            + encoded_text
        )

        response = requests.get(
            url,
            headers=pollinations_headers(),
            params={
                "voice":
                    "nova"
            },
            timeout=300
        )

        response.raise_for_status()

        with open(
            filepath,
            "wb"
        ) as file:

            file.write(
                response.content
            )

        save_generation(
            user_id,
            "audio",
            text,
            AUDIO_COST
        )

        return jsonify(
            {
                "success":
                    True,

                "type":
                    "audio",

                "credits":
                    get_credits(user_id),

                "url":
                    "/generated/"
                    + filename
            }
        )

    except Exception as e:

        print(
            "AUDIO GENERATION ERROR:",
            repr(e)
        )

        add_credits(
            user_id,
            AUDIO_COST,
            "Audio generation refund"
        )

        return jsonify(
            {
                "error":
                    "Audio generation failed."
            }
        ), 500


# ============================================================
# VIDEO GENERATION
# ============================================================

@app.route(
    "/video",
    methods=["POST"]
)
@login_required
def generate_video():

    data = request.get_json(
        silent=True
    ) or {}

    prompt = str(
        data.get(
            "prompt",
            ""
        )
    ).strip()

    if not prompt:

        return jsonify(
            {
                "error":
                    "Prompt is required."
            }
        ), 400

    user_id = session.get(
        "user_id"
    )

    if get_credits(user_id) < VIDEO_COST:

        return jsonify(
            {
                "error":
                    "Not enough credits."
            }
        ), 402

    charged = use_credits(
        user_id,
        VIDEO_COST,
        "Video generation"
    )

    if not charged:

        return jsonify(
            {
                "error":
                    "Not enough credits."
            }
        ), 402

    filename = (
        uuid.uuid4().hex
        + ".mp4"
    )

    filepath = os.path.join(
        GENERATED_DIR,
        filename
    )

    try:

        encoded_prompt = quote(
            prompt,
            safe=""
        )

        url = (
            POLLINATIONS_BASE
            + "/video/"
            + encoded_prompt
        )

        response = requests.get(
            url,
            headers=pollinations_headers(),
            params={
                "model":
                    "google/veo-3.1-fast",

                "duration":
                    4,

                "aspectRatio":
                    "16:9",

                "audio":
                    "false"
            },
            timeout=600
        )

        response.raise_for_status()

        with open(
            filepath,
            "wb"
        ) as file:

            file.write(
                response.content
            )

        save_generation(
            user_id,
            "video",
            prompt,
            VIDEO_COST
        )

        return jsonify(
            {
                "success":
                    True,

                "type":
                    "video",

                "credits":
                    get_credits(user_id),

                "url":
                    "/generated/"
                    + filename
            }
        )

    except Exception as e:

        print(
            "VIDEO GENERATION ERROR:",
            repr(e)
        )

        add_credits(
            user_id,
            VIDEO_COST,
            "Video generation refund"
        )

        return jsonify(
            {
                "error":
                    "Video generation failed."
            }
        ), 500
    # ============================================================
# RECHARGE
# ============================================================

@app.route(
    "/recharge",
    methods=["POST"]
)
@login_required
def recharge():

    return jsonify(
        {
            "error":
                "Online payment gateway is not connected yet."
        }
    ), 501


# ============================================================
# INFORMATION ROUTES
# ============================================================

@app.route("/about")
def about():

    return jsonify(
        {
            "name":
                "My AI Studio",

            "description":
                "AI Image, Video and Audio Studio"
        }
    )


@app.route("/contact")
def contact():

    return jsonify(
        {
            "message":
                "Contact My AI Studio"
        }
    )


@app.route("/privacy")
def privacy():

    return jsonify(
        {
            "privacy":
                "Privacy information for My AI Studio"
        }
    )


@app.route("/terms")
def terms():

    return jsonify(
        {
            "terms":
                "Terms and conditions for My AI Studio"
        }
    )


@app.route("/robots.txt")
def robots():

    return (
        "User-agent: *\n"
        "Allow: /\n"
    ), 200, {
        "Content-Type":
            "text/plain"
    }


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify(
        {
            "error":
                "Page not found"
        }
    ), 404


@app.errorhandler(500)
def internal_error(error):

    print(
        "INTERNAL SERVER ERROR:",
        repr(error)
    )

    return jsonify(
        {
            "error":
                "Internal server error"
        }
    ), 500


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )