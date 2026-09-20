import os
import sqlite3
import uuid
from functools import wraps
from urllib.parse import quote

import requests

from flask import (
    Flask,
    request,
    jsonify,
    session,
    render_template,
    send_from_directory,
    Response,
    g
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)


# =========================================================
# APP CONFIG
# =========================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "my-ai-studio-secret-key-2026"
)

NEW_USER_CREDITS = 100

IMAGE_COST = 5
AUDIO_COST = 8
VIDEO_COST = 21

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    ""
).strip()

POLLINATIONS_API_KEY = os.getenv(
    "POLLINATIONS_API_KEY",
    ""
).strip()

POLLINATIONS_BASE = "https://gen.pollinations.ai"

ADMIN_USERNAME = os.getenv(
    "ADMIN_USERNAME",
    ""
).strip().lower()

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


# =========================================================
# DATABASE
# =========================================================

def using_postgres():
    return bool(DATABASE_URL)


def placeholder():
    if using_postgres():
        return "%s"
    return "?"


def get_db():

    if "db" in g:
        return g.db

    if using_postgres():

        import psycopg2
        from psycopg2.extras import RealDictCursor

        g.db = psycopg2.connect(
            DATABASE_URL,
            cursor_factory=RealDictCursor
        )

    else:

        g.db = sqlite3.connect(
            "database.db",
            check_same_thread=False
        )

        g.db.row_factory = sqlite3.Row

    return g.db


@app.teardown_appcontext
def close_db(error=None):

    db = g.pop("db", None)

    if db is not None:

        try:
            db.close()
        except Exception:
            pass


def init_database():

    db = get_db()
    cursor = db.cursor()

    if using_postgres():

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                credits INTEGER NOT NULL DEFAULT 100,
                is_admin INTEGER NOT NULL DEFAULT 0,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS generation_history (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL,
                type TEXT NOT NULL,
                prompt TEXT,
                credits INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS credit_history (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT,
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
                is_blocked INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS generation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                type TEXT NOT NULL,
                prompt TEXT,
                credits INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS credit_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    # Promote configured admin account
    if ADMIN_USERNAME:

        p = placeholder()

        cursor.execute(
            f"""
            UPDATE users
            SET is_admin = 1
            WHERE username = {p}
            """,
            (ADMIN_USERNAME,)
        )

    db.commit()
    cursor.close()


# =========================================================
# USER HELPERS
# =========================================================

def get_user_by_username(username):

    if not username:
        return None

    db = get_db()
    cursor = db.cursor()

    p = placeholder()

    cursor.execute(
        f"""
        SELECT
            id,
            username,
            password,
            credits,
            is_admin,
            is_blocked,
            created_at
        FROM users
        WHERE username = {p}
        """,
        (username.lower(),)
    )

    user = cursor.fetchone()

    cursor.close()

    return user


def get_current_user():

    username = session.get("username")

    if not username:
        return None

    return get_user_by_username(
        username
    )


def get_username():

    user = get_current_user()

    if not user:
        return None

    return user["username"]


def get_credits(username):

    user = get_user_by_username(
        username
    )

    if not user:
        return 0

    return int(user["credits"])


# =========================================================
# CREDIT HISTORY
# =========================================================

def add_credit_history(
    username,
    amount,
    reason
):

    db = get_db()
    cursor = db.cursor()

    p = placeholder()

    cursor.execute(
        f"""
        INSERT INTO credit_history
        (
            username,
            amount,
            reason
        )
        VALUES (
            {p},
            {p},
            {p}
        )
        """,
        (
            username,
            amount,
            reason
        )
    )

    cursor.close()


# =========================================================
# GENERATION HISTORY
# =========================================================

def add_generation_history(
    username,
    generation_type,
    prompt,
    credits
):

    db = get_db()
    cursor = db.cursor()

    p = placeholder()

    cursor.execute(
        f"""
        INSERT INTO generation_history
        (
            username,
            type,
            prompt,
            credits
        )
        VALUES (
            {p},
            {p},
            {p},
            {p}
        )
        """,
        (
            username,
            generation_type,
            prompt,
            credits
        )
    )

    cursor.close()


# =========================================================
# CREDIT DEDUCTION
# =========================================================

def deduct_credits(
    username,
    amount
):

    db = get_db()
    cursor = db.cursor()

    p = placeholder()

    cursor.execute(
        f"""
        SELECT credits
        FROM users
        WHERE username = {p}
        """,
        (username,)
    )

    user = cursor.fetchone()

    if not user:

        cursor.close()
        return False

    current = int(
        user["credits"]
    )

    if current < amount:

        cursor.close()
        return False

    cursor.execute(
        f"""
        UPDATE users
        SET credits = {p}
        WHERE username = {p}
        """,
        (
            current - amount,
            username
        )
    )

    cursor.close()

    return True


# =========================================================
# AUTH
# =========================================================

def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        user = get_current_user()

        if not user:

            return jsonify({
                "error":
                    "Please login first."
            }), 401

        if int(
            user["is_blocked"]
        ) == 1:

            session.clear()

            return jsonify({
                "error":
                    "Your account is blocked."
            }), 403

        return function(
            *args,
            **kwargs
        )

    return wrapper


def admin_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        user = get_current_user()

        if not user:

            return jsonify({
                "error":
                    "Please login first."
            }), 401

        if int(
            user["is_blocked"]
        ) == 1:

            session.clear()

            return jsonify({
                "error":
                    "Your account is blocked."
            }), 403

        if int(
            user["is_admin"]
        ) != 1:

            return jsonify({
                "error":
                    "Admin access required."
            }), 403

        return function(
            *args,
            **kwargs
        )

    return wrapper


# =========================================================
# POLLINATIONS HELPERS
# =========================================================

def pollinations_headers():

    if not POLLINATIONS_API_KEY:

        raise RuntimeError(
            "Pollinations API key is not configured"
        )

    return {
        "Authorization":
            "Bearer " +
            POLLINATIONS_API_KEY
    }


def pollinations_error(response):

    try:

        data = response.json()

        if isinstance(
            data,
            dict
        ):

            if data.get("error"):
                return str(
                    data["error"]
                )

            if data.get("message"):
                return str(
                    data["message"]
                )

    except Exception:
        pass

    text = response.text.strip()

    if text:
        return text[:500]

    return (
        "Pollinations request failed. "
        f"HTTP {response.status_code}"
    )


def save_generated_file(
    response,
    extension
):

    filename = (
        uuid.uuid4().hex
        + "."
        + extension
    )

    filepath = os.path.join(
        GENERATED_DIR,
        filename
    )

    with open(
        filepath,
        "wb"
    ) as file:

        file.write(
            response.content
        )

    return filename
# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return render_template(
        "index.html"
    )


# =========================================================
# REGISTER
# =========================================================

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
    ).strip().lower()

    password = str(
        data.get(
            "password",
            ""
        )
    )

    if not username or not password:

        return jsonify({
            "error":
                "Username and password are required."
        }), 400

    if len(username) < 3:

        return jsonify({
            "error":
                "Username must be at least 3 characters."
        }), 400

    if len(password) < 4:

        return jsonify({
            "error":
                "Password must be at least 4 characters."
        }), 400

    if get_user_by_username(
        username
    ):

        return jsonify({
            "error":
                "Username already exists."
        }), 409

    db = get_db()
    cursor = db.cursor()

    p = placeholder()

    is_admin = 0

    if (
        ADMIN_USERNAME
        and username == ADMIN_USERNAME
    ):

        is_admin = 1

    cursor.execute(
        f"""
        INSERT INTO users
        (
            username,
            password,
            credits,
            is_admin,
            is_blocked
        )
        VALUES (
            {p},
            {p},
            {p},
            {p},
            {p}
        )
        """,
        (
            username,
            generate_password_hash(
                password
            ),
            NEW_USER_CREDITS,
            is_admin,
            0
        )
    )

    add_credit_history(
        username,
        NEW_USER_CREDITS,
        "New account bonus"
    )

    db.commit()
    cursor.close()

    return jsonify({
        "message":
            "Account created successfully."
    })


# =========================================================
# LOGIN
# =========================================================

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
    ).strip().lower()

    password = str(
        data.get(
            "password",
            ""
        )
    )

    user = get_user_by_username(
        username
    )

    if not user:

        return jsonify({
            "error":
                "Invalid username or password."
        }), 401

    if int(
        user["is_blocked"]
    ) == 1:

        return jsonify({
            "error":
                "Your account is blocked."
        }), 403

    if not check_password_hash(
        user["password"],
        password
    ):

        return jsonify({
            "error":
                "Invalid username or password."
        }), 401

    session["username"] = (
        user["username"]
    )

    return jsonify({

        "username":
            user["username"],

        "credits":
            int(user["credits"]),

        "is_admin":
            bool(user["is_admin"])

    })


# =========================================================
# ME
# =========================================================

@app.route("/me")
def me():

    user = get_current_user()

    if not user:

        return jsonify({
            "logged_in": False
        })

    if int(
        user["is_blocked"]
    ) == 1:

        session.clear()

        return jsonify({
            "logged_in": False
        })

    return jsonify({

        "logged_in":
            True,

        "username":
            user["username"],

        "credits":
            int(user["credits"]),

        "is_admin":
            bool(user["is_admin"]),

        "is_blocked":
            bool(user["is_blocked"])

    })


# =========================================================
# CREDITS
# =========================================================

@app.route("/credits")
@login_required
def credits():

    username = get_username()

    return jsonify({
        "credits":
            get_credits(username)
    })


# =========================================================
# LOGOUT
# =========================================================

@app.route(
    "/logout",
    methods=["POST"]
)
def logout():

    session.clear()

    return jsonify({
        "message":
            "Logged out."
    })


# =========================================================
# IMAGE GENERATOR
# =========================================================

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

        return jsonify({
            "error":
                "Please enter a prompt."
        }), 400

    username = get_username()

    if get_credits(username) < IMAGE_COST:

        return jsonify({
            "error":
                "Not enough credits."
        }), 400

    try:

        encoded_prompt = quote(
            prompt,
            safe=""
        )

        image_url = (
            POLLINATIONS_BASE
            + "/image/"
            + encoded_prompt
        )

        response = requests.get(
            image_url,
            params={
                "model":
                    "black-forest-labs/flux.1-schnell",

                "width":
                    1024,

                "height":
                    1024
            },
            headers=pollinations_headers(),
            timeout=180
        )

        if response.status_code != 200:

            return jsonify({
                "error":
                    pollinations_error(
                        response
                    )
            }), 502

        content_type = (
            response.headers
            .get(
                "Content-Type",
                ""
            )
            .lower()
        )

        if not content_type.startswith(
            "image/"
        ):

            return jsonify({
                "error":
                    "Image was not returned by Pollinations."
            }), 502

        filename = save_generated_file(
            response,
            "png"
        )

        if not deduct_credits(
            username,
            IMAGE_COST
        ):

            return jsonify({
                "error":
                    "Not enough credits."
            }), 400

        add_generation_history(
            username,
            "image",
            prompt,
            IMAGE_COST
        )

        db = get_db()
        db.commit()

        return jsonify({

            "image_url":
                "/generated/"
                + filename,

            "credits":
                get_credits(username)

        })

    except Exception as error:

        print(
            "IMAGE ERROR:",
            str(error)
        )

        return jsonify({
            "error":
                str(error)
        }), 500


# =========================================================
# AUDIO GENERATOR
# =========================================================

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

        return jsonify({
            "error":
                "Please enter text."
        }), 400

    username = get_username()

    if get_credits(username) < AUDIO_COST:

        return jsonify({
            "error":
                "Not enough credits."
        }), 400

    try:

        encoded_text = quote(
            text,
            safe=""
        )

        audio_url = (
            POLLINATIONS_BASE
            + "/audio/"
            + encoded_text
        )

        response = requests.get(
            audio_url,
            params={
                "voice":
                    "nova"
            },
            headers=pollinations_headers(),
            timeout=180
        )

        if response.status_code != 200:

            return jsonify({
                "error":
                    pollinations_error(
                        response
                    )
            }), 502

        content_type = (
            response.headers
            .get(
                "Content-Type",
                ""
            )
            .lower()
        )

        if (
            "audio" not in content_type
            and len(response.content) < 1000
        ):

            return jsonify({
                "error":
                    "Audio was not returned by Pollinations."
            }), 502

        filename = save_generated_file(
            response,
            "mp3"
        )

        if not deduct_credits(
            username,
            AUDIO_COST
        ):

            return jsonify({
                "error":
                    "Not enough credits."
            }), 400

        add_generation_history(
            username,
            "audio",
            text,
            AUDIO_COST
        )

        db = get_db()
        db.commit()

        return jsonify({

            "audio_url":
                "/generated/"
                + filename,

            "credits":
                get_credits(username)

        })

    except Exception as error:

        print(
            "AUDIO ERROR:",
            str(error)
        )

        return jsonify({
            "error":
                str(error)
        }), 500


# =========================================================
# VIDEO GENERATOR
# =========================================================

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

        return jsonify({
            "error":
                "Please enter a prompt."
        }), 400

    username = get_username()

    if get_credits(username) < VIDEO_COST:

        return jsonify({
            "error":
                "Not enough credits."
        }), 400

    try:

        encoded_prompt = quote(
            prompt,
            safe=""
        )

        video_url = (
            POLLINATIONS_BASE
            + "/video/"
            + encoded_prompt
        )

        response = requests.get(
            video_url,
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
            headers=pollinations_headers(),
            timeout=600
        )

        if response.status_code != 200:

            return jsonify({
                "error":
                    pollinations_error(
                        response
                    )
            }), 502

        content_type = (
            response.headers
            .get(
                "Content-Type",
                ""
            )
            .lower()
        )

        if (
            "video" not in content_type
            and len(response.content) < 10000
        ):

            return jsonify({
                "error":
                    "Video was not returned by Pollinations."
            }), 502

        filename = save_generated_file(
            response,
            "mp4"
        )

        if not deduct_credits(
            username,
            VIDEO_COST
        ):

            return jsonify({
                "error":
                    "Not enough credits."
            }), 400

        add_generation_history(
            username,
            "video",
            prompt,
            VIDEO_COST
        )

        db = get_db()
        db.commit()

        return jsonify({

            "video_url":
                "/generated/"
                + filename,

            "credits":
                get_credits(username)

        })

    except Exception as error:

        print(
            "VIDEO ERROR:",
            str(error)
        )

        return jsonify({
            "error":
                str(error)
        }), 500
    # =========================================================
# GENERATED FILES
# =========================================================

@app.route(
    "/generated/<path:filename>"
)
def generated_file(filename):

    return send_from_directory(
        GENERATED_DIR,
        filename
    )


# =========================================================
# USER HISTORY
# =========================================================

@app.route("/history")
@login_required
def user_history():

    username = get_username()

    db = get_db()
    cursor = db.cursor()

    p = placeholder()

    cursor.execute(
        f"""
        SELECT
            id,
            type,
            prompt,
            credits,
            created_at
        FROM generation_history
        WHERE username = {p}
        ORDER BY id DESC
        LIMIT 100
        """,
        (username,)
    )

    rows = cursor.fetchall()

    cursor.close()

    history = []

    for row in rows:

        history.append({

            "id":
                row["id"],

            "type":
                row["type"],

            "prompt":
                row["prompt"] or "",

            "credits":
                int(row["credits"]),

            "created_at":
                str(
                    row["created_at"]
                )

        })

    return jsonify({
        "history":
            history
    })


# =========================================================
# ADMIN USERS
# =========================================================

@app.route(
    "/admin/users"
)
@admin_required
def admin_users():

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT
            id,
            username,
            credits,
            is_admin,
            is_blocked,
            created_at
        FROM users
        ORDER BY id DESC
        """
    )

    rows = cursor.fetchall()

    cursor.close()

    users = []

    for row in rows:

        users.append({

            "id":
                row["id"],

            "username":
                row["username"],

            "credits":
                int(row["credits"]),

            "is_admin":
                bool(row["is_admin"]),

            "is_blocked":
                bool(row["is_blocked"]),

            "created_at":
                str(
                    row["created_at"]
                )

        })

    return jsonify({
        "users":
            users
    })


# =========================================================
# ADMIN CREDIT ADJUSTMENT
# =========================================================

@app.route(
    "/admin/credits",
    methods=["POST"]
)
@admin_required
def admin_credits():

    data = request.get_json(
        silent=True
    ) or {}

    username = str(
        data.get(
            "username",
            ""
        )
    ).strip().lower()

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

        return jsonify({
            "error":
                "Username is required."
        }), 400

    if amount == 0:

        return jsonify({
            "error":
                "Amount cannot be zero."
        }), 400

    user = get_user_by_username(
        username
    )

    if not user:

        return jsonify({
            "error":
                "User not found."
        }), 404

    db = get_db()
    cursor = db.cursor()

    p = placeholder()

    cursor.execute(
        f"""
        UPDATE users
        SET credits = credits + {p}
        WHERE username = {p}
        """,
        (
            amount,
            username
        )
    )

    add_credit_history(
        username,
        amount,
        "Admin credit adjustment"
    )

    db.commit()
    cursor.close()

    return jsonify({

        "message":
            "Credits updated.",

        "credits":
            get_credits(username)

    })


# =========================================================
# ADMIN BLOCK / UNBLOCK
# =========================================================

@app.route(
    "/admin/block",
    methods=["POST"]
)
@admin_required
def admin_block():

    data = request.get_json(
        silent=True
    ) or {}

    username = str(
        data.get(
            "username",
            ""
        )
    ).strip().lower()

    if not username:

        return jsonify({
            "error":
                "Username is required."
        }), 400

    user = get_user_by_username(
        username
    )

    if not user:

        return jsonify({
            "error":
                "User not found."
        }), 404

    if int(
        user["is_admin"]
    ) == 1:

        return jsonify({
            "error":
                "Admin cannot be blocked."
        }), 400

    new_status = (
        0
        if int(
            user["is_blocked"]
        ) == 1
        else 1
    )

    db = get_db()
    cursor = db.cursor()

    p = placeholder()

    cursor.execute(
        f"""
        UPDATE users
        SET is_blocked = {p}
        WHERE username = {p}
        """,
        (
            new_status,
            username
        )
    )

    db.commit()
    cursor.close()

    return jsonify({

        "message":
            (
                "User blocked."
                if new_status == 1
                else
                "User unblocked."
            ),

        "is_blocked":
            bool(new_status)

    })


# =========================================================
# ADMIN GENERATIONS
# =========================================================

@app.route(
    "/admin/generations"
)
@admin_required
def admin_generations():

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT
            id,
            username,
            type,
            prompt,
            credits,
            created_at
        FROM generation_history
        ORDER BY id DESC
        LIMIT 500
        """
    )

    rows = cursor.fetchall()

    cursor.close()

    history = []

    for row in rows:

        history.append({

            "id":
                row["id"],

            "username":
                row["username"],

            "type":
                row["type"],

            "prompt":
                row["prompt"] or "",

            "credits":
                int(row["credits"]),

            "created_at":
                str(
                    row["created_at"]
                )

        })

    return jsonify({

        "history":
            history,

        "generations":
            history

    })


# =========================================================
# ADMIN CREDIT HISTORY
# =========================================================

@app.route(
    "/admin/credit-history"
)
@admin_required
def admin_credit_history():

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT
            id,
            username,
            amount,
            reason,
            created_at
        FROM credit_history
        ORDER BY id DESC
        LIMIT 500
        """
    )

    rows = cursor.fetchall()

    cursor.close()

    history = []

    for row in rows:

        history.append({

            "id":
                row["id"],

            "username":
                row["username"],

            "amount":
                int(row["amount"]),

            "reason":
                row["reason"] or "",

            "created_at":
                str(
                    row["created_at"]
                )

        })

    return jsonify({
        "history":
            history
    })


# =========================================================
# ADMIN CREDIT HISTORY ALIAS
# =========================================================

@app.route(
    "/admin/credits/history"
)
@admin_required
def admin_credit_history_alias():

    return admin_credit_history()


# =========================================================
# ADMIN STATS
# =========================================================

@app.route(
    "/admin/stats"
)
@admin_required
def admin_stats():

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM users
        """
    )

    users = cursor.fetchone()

    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM generation_history
        """
    )

    generations = cursor.fetchone()

    cursor.execute(
        """
        SELECT COALESCE(
            SUM(credits),
            0
        ) AS total
        FROM users
        """
    )

    credits = cursor.fetchone()

    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM users
        WHERE is_blocked = 1
        """
    )

    blocked = cursor.fetchone()

    cursor.close()

    return jsonify({

        "users":
            int(
                users["total"]
            ),

        "generations":
            int(
                generations["total"]
            ),

        "credits":
            int(
                credits["total"]
            ),

        "blocked":
            int(
                blocked["total"]
            )

    })


# =========================================================
# HEALTH
# =========================================================

@app.route("/health")
def health():

    return jsonify({
        "status":
            "ok"
    })


# =========================================================
# ROBOTS
# =========================================================

@app.route("/robots.txt")
def robots():

    return Response(
        "User-agent: *\n"
        "Allow: /\n",
        mimetype="text/plain"
    )


# =========================================================
# SIMPLE SEO PAGES
# =========================================================

@app.route("/about")
def about():

    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>About - My AI Studio</title>
        <meta name="description"
              content="About My AI Studio">
    </head>
    <body>
        <h1>My AI Studio</h1>
        <p>
            AI image, video and audio creation studio.
        </p>
    </body>
    </html>
    """


@app.route("/contact")
def contact():

    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Contact - My AI Studio</title>
    </head>
    <body>
        <h1>Contact</h1>
        <p>
            Contact My AI Studio support.
        </p>
    </body>
    </html>
    """


@app.route("/privacy")
def privacy():

    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Privacy Policy - My AI Studio</title>
    </head>
    <body>
        <h1>Privacy Policy</h1>
        <p>
            My AI Studio stores account information
            required to provide the service.
        </p>
    </body>
    </html>
    """


@app.route("/terms")
def terms():

    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Terms - My AI Studio</title>
    </head>
    <body>
        <h1>Terms of Service</h1>
        <p>
            Use My AI Studio responsibly.
        </p>
    </body>
    </html>
    """


# =========================================================
# DATABASE STARTUP
# =========================================================

with app.app_context():

    init_database()


# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "5000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=True
    )