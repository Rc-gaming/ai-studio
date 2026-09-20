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
    render_template
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)


# ============================================================
# APP CONFIG
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "my-ai-studio-secret-2026"
)

app.config["SESSION_COOKIE_NAME"] = "my_ai_studio_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 30


# ============================================================
# CREDITS
# ============================================================

NEW_USER_CREDITS = 100

IMAGE_COST = 5
AUDIO_COST = 8
VIDEO_COST = 21


# ============================================================
# DATABASE
# ============================================================

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    ""
).strip()

USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor


# ============================================================
# POLLINATIONS
# ============================================================

POLLINATIONS_API_KEY = os.environ.get(
    "POLLINATIONS_API_KEY",
    ""
).strip()

POLLINATIONS_BASE = "https://gen.pollinations.ai"


# ============================================================
# ADMIN
# ============================================================

ADMIN_USERNAME = os.environ.get(
    "ADMIN_USERNAME",
    "CHANDANADMIN"
).strip()


# ============================================================
# GENERATED FILES
# ============================================================

GENERATED_DIR = os.path.join(
    os.path.dirname(
        os.path.abspath(__file__)
    ),
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

        return psycopg2.connect(
            DATABASE_URL,
            cursor_factory=RealDictCursor
        )

    return sqlite3.connect(
        os.path.join(
            os.path.dirname(
                os.path.abspath(__file__)
            ),
            "users.db"
        )
    )


def db_execute(
    query,
    params=(),
    fetchone=False,
    fetchall=False,
    commit=False
):

    con = db_connection()

    try:

        cur = con.cursor()

        cur.execute(
            query,
            params
        )

        result = None

        if fetchone:
            result = cur.fetchone()

        elif fetchall:
            result = cur.fetchall()

        if commit:
            con.commit()

        return result

    finally:

        con.close()


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

    if index is not None:

        try:
            return row[index]
        except Exception:
            return default

    return default


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_database():

    con = db_connection()

    try:

        cur = con.cursor()

        if USE_POSTGRES:

            cur.execute(
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

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_history (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    credits INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            cur.execute(
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

            con.commit()

            if ADMIN_USERNAME:

                cur.execute(
                    """
                    UPDATE users
                    SET
                        is_admin = TRUE,
                        blocked = FALSE
                    WHERE username = %s
                    """,
                    (
                        ADMIN_USERNAME,
                    )
                )

                con.commit()

        else:

            cur.execute(
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

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    credits INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            cur.execute(
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

            con.commit()

            if ADMIN_USERNAME:

                cur.execute(
                    """
                    UPDATE users
                    SET
                        is_admin = 1,
                        blocked = 0
                    WHERE username = ?
                    """,
                    (
                        ADMIN_USERNAME,
                    )
                )

                con.commit()

    finally:

        con.close()


init_database()


# ============================================================
# USER FUNCTIONS
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
        (
            user_id,
        ),
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
        (
            username,
        ),
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
    )


# ============================================================
# AUTH DECORATORS
# ============================================================

def login_required(fn):

    @wraps(fn)
    def wrapper(*args, **kwargs):

        if not session.get("user_id"):

            return jsonify(
                {
                    "error":
                        "Please login first"
                }
            ), 401

        return fn(
            *args,
            **kwargs
        )

    return wrapper


def admin_required(fn):

    @wraps(fn)
    def wrapper(*args, **kwargs):

        user_id = session.get(
            "user_id"
        )

        if not user_id:

            return jsonify(
                {
                    "error":
                        "Please login first"
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
                        "User account not found"
                }
            ), 401

        username = row_value(
            user,
            "username",
            1,
            ""
        )

        is_admin = bool(
            row_value(
                user,
                "is_admin",
                4,
                False
            )
        )

        if username == ADMIN_USERNAME:
            is_admin = True

        if not is_admin:

            return jsonify(
                {
                    "error":
                        "Admin access required"
                }
            ), 403

        return fn(
            *args,
            **kwargs
        )

    return wrapper
# ============================================================
# CREDIT FUNCTIONS
# ============================================================

def add_credits(
    user_id,
    amount,
    reason="Credit adjustment"
):

    placeholder = db_placeholder()

    con = db_connection()

    try:

        cur = con.cursor()

        cur.execute(
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

        cur.execute(
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

        con.commit()

    finally:

        con.close()


def use_credits(
    user_id,
    amount,
    reason
):

    placeholder = db_placeholder()

    con = db_connection()

    try:

        cur = con.cursor()

        cur.execute(
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

        if cur.rowcount != 1:

            con.rollback()

            return False

        cur.execute(
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

        con.commit()

        return True

    except Exception:

        con.rollback()

        raise

    finally:

        con.close()


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
# POLLINATIONS
# ============================================================

def require_pollinations_key():

    if not POLLINATIONS_API_KEY:

        raise RuntimeError(
            "Pollinations API key is not configured."
        )


def pollinations_headers():

    return {
        "Authorization":
            "Bearer " +
            POLLINATIONS_API_KEY,

        "Accept":
            "*/*"
    }


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    return render_template(
        "index.html"
    )


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return jsonify(
        {
            "status": "ok",
            "database":
                "postgres"
                if USE_POSTGRES
                else "sqlite"
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

    try:

        data = (
            request.get_json()
            or {}
        )

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
                        "Username and password are required"
                }
            ), 400

        if len(username) < 3:

            return jsonify(
                {
                    "error":
                        "Username must be at least 3 characters"
                }
            ), 400

        if len(password) < 6:

            return jsonify(
                {
                    "error":
                        "Password must be at least 6 characters"
                }
            ), 400

        if get_user_by_username(
            username
        ):

            return jsonify(
                {
                    "error":
                        "Username already exists"
                }
            ), 400

        password_hash = (
            generate_password_hash(
                password
            )
        )

        placeholder = db_placeholder()

        con = db_connection()

        try:

            cur = con.cursor()

            if USE_POSTGRES:

                cur.execute(
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
                        password_hash,
                        NEW_USER_CREDITS,
                        db_bool(
                            username ==
                            ADMIN_USERNAME
                        ),
                        db_bool(False)
                    )
                )

            else:

                cur.execute(
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
                        password_hash,
                        NEW_USER_CREDITS,
                        1 if
                        username ==
                        ADMIN_USERNAME
                        else 0,
                        0
                    )
                )

            con.commit()

        finally:

            con.close()

        return jsonify(
            {
                "message":
                    "Account created successfully",

                "credits":
                    NEW_USER_CREDITS
            }
        )

    except Exception as e:

        print(
            "REGISTER ERROR:",
            repr(e)
        )

        return jsonify(
            {
                "error":
                    str(e)
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

    try:

        data = (
            request.get_json()
            or {}
        )

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
                        "Username and password are required"
                }
            ), 400

        user = get_user_by_username(
            username
        )

        if not user:

            return jsonify(
                {
                    "error":
                        "Username or password is incorrect"
                }
            ), 401

        user_id = row_value(
            user,
            "id",
            0
        )

        stored_password = row_value(
            user,
            "password",
            2,
            ""
        )

        if not check_password_hash(
            stored_password,
            password
        ):

            return jsonify(
                {
                    "error":
                        "Username or password is incorrect"
                }
            ), 401

        is_admin = bool(
            row_value(
                user,
                "is_admin",
                4,
                False
            )
        )

        blocked = bool(
            row_value(
                user,
                "blocked",
                5,
                False
            )
        )

        # Configured admin is always admin
        # and cannot remain blocked.

        if username == ADMIN_USERNAME:

            is_admin = True
            blocked = False

            placeholder = db_placeholder()

            db_execute(
                f"""
                UPDATE users
                SET
                    is_admin =
                        {placeholder},
                    blocked =
                        {placeholder}
                WHERE id =
                    {placeholder}
                """,
                (
                    db_bool(True),
                    db_bool(False),
                    user_id
                ),
                commit=True
            )

        if blocked:

            return jsonify(
                {
                    "error":
                        "Your account is blocked."
                }
            ), 403

        # ----------------------------------------------------
        # IMPORTANT SESSION FIX
        # ----------------------------------------------------

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

        credits = get_credits(
            user_id
        )

        print(
            "LOGIN SUCCESS:",
            username,
            "USER_ID:",
            user_id,
            "ADMIN:",
            is_admin
        )

        return jsonify(
            {
                "message":
                    "Login successful",

                "logged_in":
                    True,

                "username":
                    username,

                "credits":
                    credits,

                "is_admin":
                    is_admin
            }
        )

    except Exception as e:

        print(
            "LOGIN ERROR:",
            repr(e)
        )

        return jsonify(
            {
                "error":
                    str(e)
            }
        ), 500


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

    username = row_value(
        user,
        "username",
        1,
        ""
    )

    blocked = bool(
        row_value(
            user,
            "blocked",
            5,
            False
        )
    )

    if blocked:

        session.clear()

        return jsonify(
            {
                "logged_in":
                    False,

                "error":
                    "Your account is blocked."
            }
        ), 403

    is_admin = bool(
        row_value(
            user,
            "is_admin",
            4,
            False
        )
    )

    if username == ADMIN_USERNAME:

        is_admin = True

    return jsonify(
        {
            "logged_in":
                True,

            "username":
                username,

            "credits":
                get_credits(
                    user_id
                ),

            "is_admin":
                is_admin
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

    username = session.get(
        "username"
    )

    session.clear()

    print(
        "LOGOUT:",
        username
    )

    return jsonify(
        {
            "message":
                "Logged out successfully"
        }
    )


# ============================================================
# CREDITS
# ============================================================

@app.route("/credits")
@login_required
def credits():

    return jsonify(
        {
            "credits":
                get_credits(
                    session["user_id"]
                )
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

    user_id = session[
        "user_id"
    ]

    data = (
        request.get_json()
        or {}
    )

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
                    "Please enter a prompt"
            }
        ), 400

    if get_credits(
        user_id
    ) < IMAGE_COST:

        return jsonify(
            {
                "error":
                    "Your credits are finished. Please recharge to continue."
            }
        ), 402

    charged = False

    try:

        require_pollinations_key()

        charged = use_credits(
            user_id,
            IMAGE_COST,
            "AI image generation"
        )

        if not charged:

            return jsonify(
                {
                    "error":
                        "Not enough credits"
                }
            ), 402

        encoded_prompt = quote(
            prompt,
            safe=""
        )

        url = (
            POLLINATIONS_BASE +
            "/image/" +
            encoded_prompt
        )

        params = {
            "model":
                "black-forest-labs/flux.1-schnell",

            "width":
                1024,

            "height":
                1024,

            "n":
                1
        }

        response = requests.get(
            url,
            params=params,
            headers=
                pollinations_headers(),
            timeout=300
        )

        print(
            "IMAGE STATUS:",
            response.status_code
        )

        if response.status_code != 200:

            raise RuntimeError(
                "Image API error: HTTP " +
                str(
                    response.status_code
                ) +
                " - " +
                response.text[:500]
            )

        content_type = (
            response.headers.get(
                "Content-Type",
                ""
            )
        )

        if not content_type.startswith(
            "image/"
        ):

            raise RuntimeError(
                "Image API did not return an image."
            )

        filename = (
            "image_" +
            uuid.uuid4().hex +
            ".png"
        )

        filepath = os.path.join(
            GENERATED_DIR,
            filename
        )

        with open(
            filepath,
            "wb"
        ) as f:

            f.write(
                response.content
            )

        save_generation(
            user_id,
            "image",
            prompt,
            IMAGE_COST
        )

        charged = False

        return jsonify(
            {
                "image_url":
                    "/generated/" +
                    filename,

                "credits":
                    get_credits(
                        user_id
                    )
            }
        )

    except Exception as e:

        print(
            "IMAGE ERROR:",
            repr(e)
        )

        if charged:

            try:

                add_credits(
                    user_id,
                    IMAGE_COST,
                    "Image generation failed - refund"
                )

            except Exception as refund_error:

                print(
                    "IMAGE REFUND ERROR:",
                    repr(
                        refund_error
                    )
                )

        return jsonify(
            {
                "error":
                    str(e)
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

    user_id = session[
        "user_id"
    ]

    data = (
        request.get_json()
        or {}
    )

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
                    "Please enter text"
            }
        ), 400

    if get_credits(
        user_id
    ) < AUDIO_COST:

        return jsonify(
            {
                "error":
                    "Your credits are finished. Please recharge to continue."
            }
        ), 402

    charged = False

    try:

        require_pollinations_key()

        charged = use_credits(
            user_id,
            AUDIO_COST,
            "AI audio generation"
        )

        if not charged:

            return jsonify(
                {
                    "error":
                        "Not enough credits"
                }
            ), 402

        encoded_text = quote(
            text,
            safe=""
        )

        url = (
            POLLINATIONS_BASE +
            "/audio/" +
            encoded_text
        )

        params = {
            "voice":
                "nova"
        }

        response = requests.get(
            url,
            params=params,
            headers=
                pollinations_headers(),
            timeout=300
        )

        print(
            "AUDIO STATUS:",
            response.status_code
        )

        if response.status_code != 200:

            raise RuntimeError(
                "Audio API error: HTTP " +
                str(
                    response.status_code
                ) +
                " - " +
                response.text[:500]
            )

        filename = (
            "audio_" +
            uuid.uuid4().hex +
            ".mp3"
        )

        filepath = os.path.join(
            GENERATED_DIR,
            filename
        )

        with open(
            filepath,
            "wb"
        ) as f:

            f.write(
                response.content
            )

        save_generation(
            user_id,
            "audio",
            text,
            AUDIO_COST
        )

        charged = False

        return jsonify(
            {
                "audio_url":
                    "/generated/" +
                    filename,

                "credits":
                    get_credits(
                        user_id
                    )
            }
        )

    except Exception as e:

        print(
            "AUDIO ERROR:",
            repr(e)
        )

        if charged:

            try:

                add_credits(
                    user_id,
                    AUDIO_COST,
                    "Audio generation failed - refund"
                )

            except Exception as refund_error:

                print(
                    "AUDIO REFUND ERROR:",
                    repr(
                        refund_error
                    )
                )

        return jsonify(
            {
                "error":
                    str(e)
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

    user_id = session[
        "user_id"
    ]

    data = (
        request.get_json()
        or {}
    )

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
                    "Please enter a prompt"
            }
        ), 400

    if get_credits(
        user_id
    ) < VIDEO_COST:

        return jsonify(
            {
                "error":
                    "Your credits are finished. Please recharge to continue."
            }
        ), 402

    charged = False

    try:

        require_pollinations_key()

        charged = use_credits(
            user_id,
            VIDEO_COST,
            "AI video generation"
        )

        if not charged:

            return jsonify(
                {
                    "error":
                        "Not enough credits"
                }
            ), 402

        encoded_prompt = quote(
            prompt,
            safe=""
        )

        url = (
            POLLINATIONS_BASE +
            "/video/" +
            encoded_prompt
        )

        params = {
            "model":
                "google/veo-3.1-fast",

            "duration":
                4,

            "aspectRatio":
                "16:9",

            "audio":
                "false"
        }

        response = requests.get(
            url,
            params=params,
            headers=
                pollinations_headers(),
            timeout=600
        )

        print(
            "VIDEO STATUS:",
            response.status_code
        )

        if response.status_code != 200:

            raise RuntimeError(
                "Video API error: HTTP " +
                str(
                    response.status_code
                ) +
                " - " +
                response.text[:500]
            )

        content_type = (
            response.headers.get(
                "Content-Type",
                ""
            )
        )

        if "video" not in (
            content_type.lower()
        ):

            raise RuntimeError(
                "Video API did not return a video."
            )

        filename = (
            "video_" +
            uuid.uuid4().hex +
            ".mp4"
        )

        filepath = os.path.join(
            GENERATED_DIR,
            filename
        )

        with open(
            filepath,
            "wb"
        ) as f:

            f.write(
                response.content
            )

        save_generation(
            user_id,
            "video",
            prompt,
            VIDEO_COST
        )

        charged = False

        return jsonify(
            {
                "video_url":
                    "/generated/" +
                    filename,

                "credits":
                    get_credits(
                        user_id
                    )
            }
        )

    except Exception as e:

        print(
            "VIDEO ERROR:",
            repr(e)
        )

        if charged:

            try:

                add_credits(
                    user_id,
                    VIDEO_COST,
                    "Video generation failed - refund"
                )

            except Exception as refund_error:

                print(
                    "VIDEO REFUND ERROR:",
                    repr(
                        refund_error
                    )
                )

        return jsonify(
            {
                "error":
                    str(e)
            }
        ), 500


# ============================================================
# GENERATED FILES
# ============================================================

@app.route(
    "/generated/<path:filename>"
)
def generated_file(filename):

    safe_name = os.path.basename(
        filename
    )

    filepath = os.path.join(
        GENERATED_DIR,
        safe_name
    )

    if not os.path.isfile(
        filepath
    ):

        return jsonify(
            {
                "error":
                    "Generated file not found"
            }
        ), 404

    return send_file(
        filepath
    )


# ============================================================
# USER HISTORY
# ============================================================

@app.route("/history")
@login_required
def user_history():

    user_id = session[
        "user_id"
    ]

    placeholder = db_placeholder()

    rows = db_execute(
        f"""
        SELECT
            type,
            prompt,
            credits,
            created_at
        FROM generation_history
        WHERE user_id =
            {placeholder}
        ORDER BY id DESC
        LIMIT 100
        """,
        (
            user_id,
        ),
        fetchall=True
    )

    history = []

    for row in rows or []:

        history.append(
            {
                "type":
                    row_value(
                        row,
                        "type",
                        0,
                        ""
                    ),

                "prompt":
                    row_value(
                        row,
                        "prompt",
                        1,
                        ""
                    ),

                "credits":
                    row_value(
                        row,
                        "credits",
                        2,
                        0
                    ),

                "created_at":
                    str(
                        row_value(
                            row,
                            "created_at",
                            3,
                            ""
                        )
                    )
            }
        )

    return jsonify(
        {
            "history":
                history
        }
    )
# ============================================================
# ADMIN STATS
# ============================================================

@app.route(
    "/admin/stats"
)
@admin_required
def admin_stats():

    con = db_connection()

    try:

        cur = con.cursor()

        cur.execute(
            "SELECT COUNT(*) AS total FROM users"
        )

        total_users = cur.fetchone()

        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM generation_history
            """
        )

        total_generations = (
            cur.fetchone()
        )

        cur.execute(
            """
            SELECT COALESCE(
                SUM(credits),
                0
            ) AS total
            FROM users
            """
        )

        total_credits = (
            cur.fetchone()
        )

        if USE_POSTGRES:

            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM users
                WHERE blocked = TRUE
                """
            )

        else:

            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM users
                WHERE blocked = 1
                """
            )

        blocked_users = (
            cur.fetchone()
        )

    finally:

        con.close()

    return jsonify(
        {
            "total_users":
                int(
                    row_value(
                        total_users,
                        "total",
                        0,
                        0
                    )
                ),

            "total_generations":
                int(
                    row_value(
                        total_generations,
                        "total",
                        0,
                        0
                    )
                ),

            "total_credits":
                int(
                    row_value(
                        total_credits,
                        "total",
                        0,
                        0
                    )
                ),

            "blocked_users":
                int(
                    row_value(
                        blocked_users,
                        "total",
                        0,
                        0
                    )
                )
        }
    )


# ============================================================
# ADMIN USERS
# ============================================================

@app.route(
    "/admin/users"
)
@admin_required
def admin_users():

    rows = db_execute(
        """
        SELECT
            id,
            username,
            credits,
            is_admin,
            blocked,
            created_at
        FROM users
        ORDER BY id DESC
        """,
        fetchall=True
    )

    users = []

    for row in rows or []:

        users.append(
            {
                "id":
                    row_value(
                        row,
                        "id",
                        0
                    ),

                "username":
                    row_value(
                        row,
                        "username",
                        1,
                        ""
                    ),

                "credits":
                    row_value(
                        row,
                        "credits",
                        2,
                        0
                    ),

                "is_admin":
                    bool(
                        row_value(
                            row,
                            "is_admin",
                            3,
                            False
                        )
                    ),

                "blocked":
                    bool(
                        row_value(
                            row,
                            "blocked",
                            4,
                            False
                        )
                    ),

                "created_at":
                    str(
                        row_value(
                            row,
                            "created_at",
                            5,
                            ""
                        )
                    )
            }
        )

    return jsonify(
        {
            "users":
                users
        }
    )


# ============================================================
# ADMIN CREDIT ADJUSTMENT
# ============================================================

@app.route(
    "/admin/credits",
    methods=["POST"]
)
@admin_required
def admin_credits():

    data = (
        request.get_json()
        or {}
    )

    username = str(
        data.get(
            "username",
            ""
        )
    ).strip()

    try:

        amount = int(
            data.get(
                "amount",
                0
            )
        )

    except Exception:

        amount = 0

    if not username:

        return jsonify(
            {
                "error":
                    "Username is required"
            }
        ), 400

    if amount == 0:

        return jsonify(
            {
                "error":
                    "Amount cannot be zero"
            }
        ), 400

    user = get_user_by_username(
        username
    )

    if not user:

        return jsonify(
            {
                "error":
                    "User not found"
            }
        ), 404

    user_id = row_value(
        user,
        "id",
        0
    )

    if amount < 0:

        current = get_credits(
            user_id
        )

        if current + amount < 0:

            return jsonify(
                {
                    "error":
                        "User cannot have negative credits"
                }
            ), 400

    add_credits(
        user_id,
        amount,
        "Admin credit adjustment"
    )

    return jsonify(
        {
            "message":
                "Credits updated successfully",

            "credits":
                get_credits(
                    user_id
                )
        }
    )


# ============================================================
# ADMIN BLOCK / UNBLOCK
# ============================================================

@app.route(
    "/admin/block",
    methods=["POST"]
)
@admin_required
def admin_block():

    data = (
        request.get_json()
        or {}
    )

    try:

        user_id = int(
            data.get(
                "user_id"
            )
        )

    except Exception:

        return jsonify(
            {
                "error":
                    "Invalid user ID"
            }
        ), 400

    blocked = bool(
        data.get(
            "blocked",
            False
        )
    )

    user = get_user_by_id(
        user_id
    )

    if not user:

        return jsonify(
            {
                "error":
                    "User not found"
            }
        ), 404

    username = row_value(
        user,
        "username",
        1,
        ""
    )

    if username == ADMIN_USERNAME:

        blocked = False

    placeholder = db_placeholder()

    db_execute(
        f"""
        UPDATE users
        SET blocked =
            {placeholder}
        WHERE id =
            {placeholder}
        """,
        (
            db_bool(
                blocked
            ),
            user_id
        ),
        commit=True
    )

    return jsonify(
        {
            "message":
                "User status updated",

            "blocked":
                blocked
        }
    )


# ============================================================
# ADMIN GENERATION HISTORY
# ============================================================

@app.route(
    "/admin/generations"
)
@admin_required
def admin_generations():

    rows = db_execute(
        """
        SELECT
            generation_history.type,
            generation_history.prompt,
            generation_history.credits,
            generation_history.created_at,
            users.username
        FROM generation_history
        JOIN users
            ON users.id =
               generation_history.user_id
        ORDER BY
            generation_history.id DESC
        LIMIT 500
        """,
        fetchall=True
    )

    generations = []

    for row in rows or []:

        generations.append(
            {
                "username":
                    row_value(
                        row,
                        "username",
                        4,
                        ""
                    ),

                "type":
                    row_value(
                        row,
                        "type",
                        0,
                        ""
                    ),

                "prompt":
                    row_value(
                        row,
                        "prompt",
                        1,
                        ""
                    ),

                "credits":
                    row_value(
                        row,
                        "credits",
                        2,
                        0
                    ),

                "created_at":
                    str(
                        row_value(
                            row,
                            "created_at",
                            3,
                            ""
                        )
                    )
            }
        )

    return jsonify(
        {
            "generations":
                generations
        }
    )


# ============================================================
# ADMIN CREDIT HISTORY
# ============================================================

@app.route(
    "/admin/credits/history"
)
@admin_required
def admin_credit_history():

    rows = db_execute(
        """
        SELECT
            credit_history.amount,
            credit_history.reason,
            credit_history.created_at,
            users.username
        FROM credit_history
        JOIN users
            ON users.id =
               credit_history.user_id
        ORDER BY
            credit_history.id DESC
        LIMIT 500
        """,
        fetchall=True
    )

    history = []

    for row in rows or []:

        history.append(
            {
                "username":
                    row_value(
                        row,
                        "username",
                        3,
                        ""
                    ),

                "amount":
                    row_value(
                        row,
                        "amount",
                        0,
                        0
                    ),

                "reason":
                    row_value(
                        row,
                        "reason",
                        1,
                        ""
                    ),

                "created_at":
                    str(
                        row_value(
                            row,
                            "created_at",
                            2,
                            ""
                        )
                    )
            }
        )

    return jsonify(
        {
            "history":
                history
        }
    )
    # ============================================================
# RECHARGE
# ============================================================

@app.route(
    "/recharge",
    methods=["POST"]
)
@login_required
def recharge():

    try:

        data = (
            request.get_json()
            or {}
        )

        amount = int(
            data.get(
                "amount",
                0
            )
        )

        if amount not in (
            100,
            500,
            1000
        ):

            return jsonify(
                {
                    "error":
                        "Invalid recharge amount"
                }
            ), 400

        add_credits(
            session["user_id"],
            amount,
            "Recharge"
        )

        return jsonify(
            {
                "message":
                    "Credits added successfully",

                "credits":
                    get_credits(
                        session["user_id"]
                    )
            }
        )

    except Exception as e:

        return jsonify(
            {
                "error":
                    str(e)
            }
        ), 500


# ============================================================
# ABOUT
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


# ============================================================
# CONTACT
# ============================================================

@app.route("/contact")
def contact():

    return jsonify(
        {
            "message":
                "Contact My AI Studio"
        }
    )


# ============================================================
# PRIVACY
# ============================================================

@app.route("/privacy")
def privacy():

    return jsonify(
        {
            "privacy":
                "Privacy information for My AI Studio"
        }
    )


# ============================================================
# TERMS
# ============================================================

@app.route("/terms")
def terms():

    return jsonify(
        {
            "terms":
                "Terms and conditions for My AI Studio"
        }
    )


# ============================================================
# ROBOTS
# ============================================================

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
# START SERVER
# ============================================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )