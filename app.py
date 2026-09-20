import os
import sqlite3
import uuid
from functools import wraps
from urllib.parse import quote

import requests
from flask import Flask, jsonify, render_template, request, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash


app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "change-this-secret-key"
)

NEW_USER_CREDITS = 100

IMAGE_COST = 5
AUDIO_COST = 8
VIDEO_COST = 21

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

POLLINATIONS_API_KEY = os.getenv(
    "POLLINATIONS_API_KEY",
    ""
).strip()

POLLINATIONS_BASE = "https://gen.pollinations.ai"

ADMIN_USERNAME = os.getenv(
    "ADMIN_USERNAME",
    ""
).strip()

GENERATED_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "generated"
)

os.makedirs(GENERATED_DIR, exist_ok=True)


# =========================================================
# DATABASE
# =========================================================

def using_postgres():
    return bool(DATABASE_URL)


def get_db():
    if using_postgres():
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(DATABASE_URL)
        return conn, True

    conn = sqlite3.connect(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "studio.db"
        )
    )

    conn.row_factory = sqlite3.Row

    return conn, False


def db_placeholder():
    if using_postgres():
        return "%s"
    return "?"


def db_bool(value):
    if using_postgres():
        return bool(value)

    return 1 if value else 0


def db_execute(query, params=(), fetchone=False, fetchall=False):
    conn, postgres = get_db()

    try:
        if postgres:
            from psycopg2.extras import RealDictCursor

            cursor = conn.cursor(
                cursor_factory=RealDictCursor
            )

            cursor.execute(query, params)

            result = None

            if fetchone:
                result = cursor.fetchone()

            elif fetchall:
                result = cursor.fetchall()

            conn.commit()
            cursor.close()

            return result

        cursor = conn.cursor()
        cursor.execute(query, params)

        result = None

        if fetchone:
            row = cursor.fetchone()
            result = dict(row) if row else None

        elif fetchall:
            rows = cursor.fetchall()
            result = [dict(row) for row in rows]

        conn.commit()

        return result

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def init_database():
    conn, postgres = get_db()

    try:
        cursor = conn.cursor()

        if postgres:

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    credits INTEGER NOT NULL DEFAULT 100,
                    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                    blocked BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS generation_history (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    credits INTEGER NOT NULL,
                    file_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS credit_history (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        else:

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    credits INTEGER NOT NULL DEFAULT 100,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    blocked INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS generation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    credits INTEGER NOT NULL,
                    file_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS credit_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        conn.commit()

    finally:
        conn.close()

    if ADMIN_USERNAME:

        placeholder = db_placeholder()

        db_execute(
            f"""
            UPDATE users
            SET is_admin = {placeholder}
            WHERE username = {placeholder}
            """,
            (
                db_bool(True),
                ADMIN_USERNAME
            )
        )


# =========================================================
# USER / AUTH
# =========================================================

def current_user():
    user_id = session.get("user_id")

    if not user_id:
        return None

    placeholder = db_placeholder()

    return db_execute(
        f"""
        SELECT
            id,
            username,
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


def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        user = current_user()

        if not user:
            return jsonify({
                "error": "Please login first."
            }), 401

        if user["blocked"]:
            session.clear()

            return jsonify({
                "error": "Your account is blocked."
            }), 403

        return function(*args, **kwargs)

    return wrapper


def admin_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        user = current_user()

        if not user:
            return jsonify({
                "error": "Please login first."
            }), 401

        if not user["is_admin"]:
            return jsonify({
                "error": "Admin access required."
            }), 403

        return function(*args, **kwargs)

    return wrapper


# =========================================================
# CREDITS
# =========================================================

def get_user_credits(user_id):

    placeholder = db_placeholder()

    row = db_execute(
        f"""
        SELECT credits
        FROM users
        WHERE id = {placeholder}
        """,
        (user_id,),
        fetchone=True
    )

    if not row:
        return 0

    return int(row["credits"])


def add_credit_history(
    user_id,
    amount,
    reason
):

    placeholder = db_placeholder()

    db_execute(
        f"""
        INSERT INTO credit_history
        (
            user_id,
            amount,
            reason
        )
        VALUES (
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


def deduct_credits(
    user_id,
    amount,
    reason
):

    current = get_user_credits(user_id)

    if current < amount:
        return False

    placeholder = db_placeholder()

    db_execute(
        f"""
        UPDATE users
        SET credits = credits - {placeholder}
        WHERE id = {placeholder}
        """,
        (
            amount,
            user_id
        )
    )

    add_credit_history(
        user_id,
        -amount,
        reason
    )

    return True


def add_credits(
    user_id,
    amount,
    reason
):

    placeholder = db_placeholder()

    db_execute(
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

    add_credit_history(
        user_id,
        amount,
        reason
    )


def add_generation_history(
    user_id,
    generation_type,
    prompt,
    credits,
    file_url
):

    placeholder = db_placeholder()

    db_execute(
        f"""
        INSERT INTO generation_history
        (
            user_id,
            type,
            prompt,
            credits,
            file_url
        )
        VALUES (
            {placeholder},
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
            credits,
            file_url
        )
    )


# =========================================================
# POLLINATIONS
# =========================================================

def pollinations_headers():

    headers = {
        "Accept": "*/*"
    }

    if POLLINATIONS_API_KEY:
        headers["Authorization"] = (
            "Bearer " + POLLINATIONS_API_KEY
        )

    return headers


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

    with open(filepath, "wb") as file:
        file.write(response.content)

    return filename


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return render_template("index.html")


# =========================================================
# REGISTER
# =========================================================

@app.route("/register", methods=["POST"])
def register():

    try:

        data = request.get_json(
            silent=True
        ) or {}

        username = str(
            data.get("username", "")
        ).strip()

        password = str(
            data.get("password", "")
        )

        if not username or not password:
            return jsonify({
                "error": "Username and password are required."
            }), 400

        if len(username) < 3:
            return jsonify({
                "error": "Username must be at least 3 characters."
            }), 400

        if len(password) < 4:
            return jsonify({
                "error": "Password must be at least 4 characters."
            }), 400

        placeholder = db_placeholder()

        existing = db_execute(
            f"""
            SELECT id
            FROM users
            WHERE username = {placeholder}
            """,
            (username,),
            fetchone=True
        )

        if existing:
            return jsonify({
                "error": "Username already exists."
            }), 409

        is_admin = False

        if ADMIN_USERNAME:
            if username == ADMIN_USERNAME:
                is_admin = True

        password_hash = generate_password_hash(
            password
        )

        db_execute(
            f"""
            INSERT INTO users
            (
                username,
                password,
                credits,
                is_admin,
                blocked
            )
            VALUES (
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
                db_bool(is_admin),
                db_bool(False)
            )
        )

        user = db_execute(
            f"""
            SELECT
                id,
                username,
                credits,
                is_admin
            FROM users
            WHERE username = {placeholder}
            """,
            (username,),
            fetchone=True
        )

        add_credit_history(
            user["id"],
            NEW_USER_CREDITS,
            "Welcome credits"
        )

        return jsonify({
            "success": True,
            "message": "Registration successful.",
            "username": username
        })

    except Exception as e:

        print(
            "REGISTER ERROR:",
            repr(e)
        )

        return jsonify({
            "error": str(e)
        }), 500


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["POST"])
def login():

    try:

        data = request.get_json(
            silent=True
        ) or {}

        username = str(
            data.get("username", "")
        ).strip()

        password = str(
            data.get("password", "")
        )

        if not username or not password:
            return jsonify({
                "error": "Username and password are required."
            }), 400

        placeholder = db_placeholder()

        user = db_execute(
            f"""
            SELECT *
            FROM users
            WHERE username = {placeholder}
            """,
            (username,),
            fetchone=True
        )

        if not user:
            return jsonify({
                "error": "Invalid username or password."
            }), 401

        if user["blocked"]:
            return jsonify({
                "error": "Your account is blocked."
            }), 403

        if not check_password_hash(
            user["password"],
            password
        ):
            return jsonify({
                "error": "Invalid username or password."
            }), 401

        if (
            ADMIN_USERNAME
            and username == ADMIN_USERNAME
            and not user["is_admin"]
        ):

            db_execute(
                f"""
                UPDATE users
                SET is_admin = {placeholder}
                WHERE id = {placeholder}
                """,
                (
                    db_bool(True),
                    user["id"]
                )
            )

            user["is_admin"] = db_bool(True)

        session.clear()

        session["user_id"] = user["id"]

        return jsonify({
            "success": True,
            "username": user["username"],
            "credits": int(user["credits"]),
            "is_admin": bool(user["is_admin"])
        })

    except Exception as e:

        print(
            "LOGIN ERROR:",
            repr(e)
        )

        return jsonify({
            "error": str(e)
        }), 500


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout", methods=["POST"])
def logout():

    session.clear()

    return jsonify({
        "success": True
    })


# =========================================================
# ME
# =========================================================

@app.route("/me")
@login_required
def me():

    user = current_user()

    return jsonify({
        "success": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "credits": int(user["credits"]),
            "is_admin": bool(user["is_admin"]),
            "blocked": bool(user["blocked"]),
            "created_at": str(
                user["created_at"]
            )
        }
    })


# =========================================================
# CREDITS
# =========================================================

@app.route("/credits")
@login_required
def credits():

    user = current_user()

    return jsonify({
        "credits": int(user["credits"])
    })
    # =========================================================
# IMAGE GENERATION
# =========================================================

@app.route("/generate", methods=["POST"])
@login_required
def generate_image():

    user = current_user()
    charged = False

    try:

        data = request.get_json(
            silent=True
        ) or {}

        prompt = str(
            data.get("prompt", "")
        ).strip()

        if not prompt:
            return jsonify({
                "error": "Please enter a prompt."
            }), 400

        if not POLLINATIONS_API_KEY:
            return jsonify({
                "error": "Pollinations API key is not configured."
            }), 500

        if not deduct_credits(
            user["id"],
            IMAGE_COST,
            "Image generation"
        ):
            return jsonify({
                "error": "Not enough credits."
            }), 402

        charged = True

        encoded_prompt = quote(
            prompt,
            safe=""
        )

        url = (
            f"{POLLINATIONS_BASE}/image/"
            f"{encoded_prompt}"
        )

        params = {
            "model": "black-forest-labs/flux.1-schnell",
            "width": 1024,
            "height": 1024,
            "nologo": "true"
        }

        response = requests.get(
            url,
            params=params,
            headers=pollinations_headers(),
            timeout=180
        )

        if response.status_code != 200:

            add_credits(
                user["id"],
                IMAGE_COST,
                "Image generation refund"
            )

            charged = False

            return jsonify({
                "error": (
                    "Image API error: "
                    + str(response.status_code)
                    + " "
                    + response.text[:500]
                )
            }), 502

        if not response.content:

            add_credits(
                user["id"],
                IMAGE_COST,
                "Image generation refund"
            )

            charged = False

            return jsonify({
                "error": "Image response was empty."
            }), 502

        content_type = response.headers.get(
            "Content-Type",
            ""
        ).lower()

        extension = "png"

        if "jpeg" in content_type:
            extension = "jpg"

        elif "jpg" in content_type:
            extension = "jpg"

        elif "webp" in content_type:
            extension = "webp"

        filename = save_generated_file(
            response,
            extension
        )

        image_url = (
            "/generated/"
            + filename
        )

        add_generation_history(
            user["id"],
            "image",
            prompt,
            IMAGE_COST,
            image_url
        )

        return jsonify({
            "success": True,
            "image_url": image_url,
            "credits": get_user_credits(
                user["id"]
            )
        })

    except Exception as e:

        print(
            "IMAGE ERROR:",
            repr(e)
        )

        if charged:

            try:
                add_credits(
                    user["id"],
                    IMAGE_COST,
                    "Image generation error refund"
                )
            except Exception:
                pass

        return jsonify({
            "error": str(e)
        }), 500


# =========================================================
# AUDIO GENERATION
# =========================================================

@app.route("/audio", methods=["POST"])
@login_required
def generate_audio():

    user = current_user()
    charged = False

    try:

        data = request.get_json(
            silent=True
        ) or {}

        text = str(
            data.get("text", "")
        ).strip()

        if not text:
            return jsonify({
                "error": "Please enter text."
            }), 400

        if not POLLINATIONS_API_KEY:
            return jsonify({
                "error": "Pollinations API key is not configured."
            }), 500

        if not deduct_credits(
            user["id"],
            AUDIO_COST,
            "Audio generation"
        ):
            return jsonify({
                "error": "Not enough credits."
            }), 402

        charged = True

        encoded_text = quote(
            text,
            safe=""
        )

        url = (
            f"{POLLINATIONS_BASE}/audio/"
            f"{encoded_text}"
        )

        params = {
            "voice": "nova"
        }

        response = requests.get(
            url,
            params=params,
            headers=pollinations_headers(),
            timeout=180
        )

        if response.status_code != 200:

            add_credits(
                user["id"],
                AUDIO_COST,
                "Audio generation refund"
            )

            charged = False

            return jsonify({
                "error": (
                    "Audio API error: "
                    + str(response.status_code)
                    + " "
                    + response.text[:500]
                )
            }), 502

        if not response.content:

            add_credits(
                user["id"],
                AUDIO_COST,
                "Audio generation refund"
            )

            charged = False

            return jsonify({
                "error": "Audio response was empty."
            }), 502

        filename = save_generated_file(
            response,
            "mp3"
        )

        audio_url = (
            "/generated/"
            + filename
        )

        add_generation_history(
            user["id"],
            "audio",
            text,
            AUDIO_COST,
            audio_url
        )

        return jsonify({
            "success": True,
            "audio_url": audio_url,
            "credits": get_user_credits(
                user["id"]
            )
        })

    except Exception as e:

        print(
            "AUDIO ERROR:",
            repr(e)
        )

        if charged:

            try:
                add_credits(
                    user["id"],
                    AUDIO_COST,
                    "Audio generation error refund"
                )
            except Exception:
                pass

        return jsonify({
            "error": str(e)
        }), 500


# =========================================================
# VIDEO GENERATION
# =========================================================

@app.route("/video", methods=["POST"])
@login_required
def generate_video():

    user = current_user()
    charged = False

    try:

        data = request.get_json(
            silent=True
        ) or {}

        prompt = str(
            data.get("prompt", "")
        ).strip()

        if not prompt:
            return jsonify({
                "error": "Please enter a prompt."
            }), 400

        if not POLLINATIONS_API_KEY:
            return jsonify({
                "error": "Pollinations API key is not configured."
            }), 500

        if not deduct_credits(
            user["id"],
            VIDEO_COST,
            "Video generation"
        ):
            return jsonify({
                "error": "Not enough credits."
            }), 402

        charged = True

        encoded_prompt = quote(
            prompt,
            safe=""
        )

        url = (
            f"{POLLINATIONS_BASE}/video/"
            f"{encoded_prompt}"
        )

        params = {
            "model": "google/veo-3.1-fast",
            "duration": 4,
            "aspectRatio": "16:9",
            "audio": "false"
        }

        response = requests.get(
            url,
            params=params,
            headers=pollinations_headers(),
            timeout=600
        )

        if response.status_code != 200:

            add_credits(
                user["id"],
                VIDEO_COST,
                "Video generation refund"
            )

            charged = False

            return jsonify({
                "error": (
                    "Video API error: "
                    + str(response.status_code)
                    + " "
                    + response.text[:500]
                )
            }), 502

        if not response.content:

            add_credits(
                user["id"],
                VIDEO_COST,
                "Video generation refund"
            )

            charged = False

            return jsonify({
                "error": "Video response was empty."
            }), 502

        filename = save_generated_file(
            response,
            "mp4"
        )

        video_url = (
            "/generated/"
            + filename
        )

        add_generation_history(
            user["id"],
            "video",
            prompt,
            VIDEO_COST,
            video_url
        )

        return jsonify({
            "success": True,
            "video_url": video_url,
            "credits": get_user_credits(
                user["id"]
            )
        })

    except Exception as e:

        print(
            "VIDEO ERROR:",
            repr(e)
        )

        if charged:

            try:
                add_credits(
                    user["id"],
                    VIDEO_COST,
                    "Video generation error refund"
                )
            except Exception:
                pass

        return jsonify({
            "error": str(e)
        }), 500


# =========================================================
# GENERATED FILES
# =========================================================

@app.route("/generated/<path:filename>")
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

    user = current_user()

    placeholder = db_placeholder()

    rows = db_execute(
        f"""
        SELECT
            type,
            prompt,
            credits,
            file_url,
            created_at
        FROM generation_history
        WHERE user_id = {placeholder}
        ORDER BY id DESC
        LIMIT 100
        """,
        (user["id"],),
        fetchall=True
    )

    history = []

    for row in rows:

        history.append({
            "type": row["type"],
            "prompt": row["prompt"],
            "credits": int(row["credits"]),
            "file_url": row["file_url"],
            "created_at": str(
                row["created_at"]
            )
        })

    return jsonify({
        "history": history
    })


# =========================================================
# ADMIN - USERS
# =========================================================

@app.route("/admin/users")
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

    for row in rows:

        users.append({
            "id": row["id"],
            "username": row["username"],
            "credits": int(row["credits"]),
            "is_admin": bool(row["is_admin"]),
            "blocked": bool(row["blocked"]),
            "created_at": str(
                row["created_at"]
            )
        })

    return jsonify({
        "users": users
    })


# =========================================================
# ADMIN - CREDIT ADJUSTMENT
# =========================================================

@app.route("/admin/credits", methods=["POST"])
@admin_required
def admin_adjust_credits():

    try:

        data = request.get_json(
            silent=True
        ) or {}

        user_id = int(
            data.get("user_id")
        )

        amount = int(
            data.get("amount")
        )

        reason = str(
            data.get(
                "reason",
                "Admin credit adjustment"
            )
        ).strip()

        if amount == 0:
            return jsonify({
                "error": "Amount cannot be zero."
            }), 400

        placeholder = db_placeholder()

        user = db_execute(
            f"""
            SELECT id, username
            FROM users
            WHERE id = {placeholder}
            """,
            (user_id,),
            fetchone=True
        )

        if not user:
            return jsonify({
                "error": "User not found."
            }), 404

        if amount > 0:

            add_credits(
                user_id,
                amount,
                reason
            )

        else:

            remove_amount = abs(amount)

            current = get_user_credits(
                user_id
            )

            if current < remove_amount:
                return jsonify({
                    "error": "User does not have enough credits."
                }), 400

            db_execute(
                f"""
                UPDATE users
                SET credits = credits - {placeholder}
                WHERE id = {placeholder}
                """,
                (
                    remove_amount,
                    user_id
                )
            )

            add_credit_history(
                user_id,
                -remove_amount,
                reason
            )

        return jsonify({
            "success": True,
            "credits": get_user_credits(
                user_id
            )
        })

    except Exception as e:

        print(
            "ADMIN CREDIT ERROR:",
            repr(e)
        )

        return jsonify({
            "error": str(e)
        }), 500
        # =========================================================
# ADMIN - BLOCK / UNBLOCK
# =========================================================

@app.route("/admin/block", methods=["POST"])
@admin_required
def admin_block_user():

    try:
        data = request.get_json(silent=True) or {}

        user_id = int(data.get("user_id"))
        blocked = bool(data.get("blocked"))

        placeholder = db_placeholder()

        db_execute(
            f"""
            UPDATE users
            SET blocked = {placeholder}
            WHERE id = {placeholder}
            """,
            (
                db_bool(blocked),
                user_id
            )
        )

        return jsonify({
            "success": True,
            "blocked": blocked
        })

    except Exception as e:

        print(
            "ADMIN BLOCK ERROR:",
            repr(e)
        )

        return jsonify({
            "error": str(e)
        }), 500


# =========================================================
# ADMIN - GENERATIONS
# =========================================================

@app.route("/admin/generations")
@admin_required
def admin_generations():

    rows = db_execute(
        """
        SELECT
            g.id,
            g.type,
            g.prompt,
            g.credits,
            g.file_url,
            g.created_at,
            u.username
        FROM generation_history g
        LEFT JOIN users u
            ON u.id = g.user_id
        ORDER BY g.id DESC
        LIMIT 500
        """,
        fetchall=True
    )

    generations = []

    for row in rows:

        generations.append({
            "id": row["id"],
            "username": (
                row["username"]
                or "Unknown"
            ),
            "type": row["type"],
            "prompt": row["prompt"],
            "credits": int(row["credits"]),
            "file_url": row["file_url"],
            "created_at": str(
                row["created_at"]
            )
        })

    return jsonify({
        "generations": generations
    })


# =========================================================
# ADMIN - CREDIT HISTORY
# =========================================================

@app.route("/admin/credits/history")
@admin_required
def admin_credit_history():

    rows = db_execute(
        """
        SELECT
            c.id,
            c.amount,
            c.reason,
            c.created_at,
            u.username
        FROM credit_history c
        LEFT JOIN users u
            ON u.id = c.user_id
        ORDER BY c.id DESC
        LIMIT 500
        """,
        fetchall=True
    )

    history = []

    for row in rows:

        history.append({
            "id": row["id"],
            "username": (
                row["username"]
                or "Unknown"
            ),
            "amount": int(row["amount"]),
            "reason": row["reason"],
            "created_at": str(
                row["created_at"]
            )
        })

    return jsonify({
        "history": history
    })
    # =========================================================
# ADMIN - STATS
# =========================================================

@app.route("/admin/stats")
@admin_required
def admin_stats():

    user_count = db_execute(
        """
        SELECT COUNT(*) AS count
        FROM users
        """,
        fetchone=True
    )

    generation_count = db_execute(
        """
        SELECT COUNT(*) AS count
        FROM generation_history
        """,
        fetchone=True
    )

    total_credits = db_execute(
        """
        SELECT COALESCE(
            SUM(credits),
            0
        ) AS total
        FROM users
        """,
        fetchone=True
    )

    image_count = db_execute(
        """
        SELECT COUNT(*) AS count
        FROM generation_history
        WHERE type = 'image'
        """,
        fetchone=True
    )

    video_count = db_execute(
        """
        SELECT COUNT(*) AS count
        FROM generation_history
        WHERE type = 'video'
        """,
        fetchone=True
    )

    audio_count = db_execute(
        """
        SELECT COUNT(*) AS count
        FROM generation_history
        WHERE type = 'audio'
        """,
        fetchone=True
    )

    return jsonify({
        "users": int(
            user_count["count"]
        ),
        "generations": int(
            generation_count["count"]
        ),
        "total_credits": int(
            total_credits["total"]
        ),
        "images": int(
            image_count["count"]
        ),
        "videos": int(
            video_count["count"]
        ),
        "audio": int(
            audio_count["count"]
        )
    })


# =========================================================
# HEALTH
# =========================================================

@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "service": "My AI Studio"
    })


# =========================================================
# ROBOTS
# =========================================================

@app.route("/robots.txt")
def robots():

    return (
        "User-agent: *\n"
        "Allow: /\n"
    ), 200, {
        "Content-Type": "text/plain"
    }


# =========================================================
# ABOUT
# =========================================================

@app.route("/about")
def about():

    return jsonify({
        "name": "My AI Studio",
        "description": (
            "AI image, video and audio generation studio."
        )
    })


# =========================================================
# CONTACT
# =========================================================

@app.route("/contact")
def contact():

    return jsonify({
        "message": "Contact My AI Studio."
    })


# =========================================================
# PRIVACY
# =========================================================

@app.route("/privacy")
def privacy():

    return jsonify({
        "message": (
            "Privacy information for My AI Studio."
        )
    })


# =========================================================
# TERMS
# =========================================================

@app.route("/terms")
def terms():

    return jsonify({
        "message": (
            "Terms and conditions for My AI Studio."
        )
    })


# =========================================================
# INITIALIZE DATABASE
# =========================================================

try:

    init_database()

    print(
        "DATABASE INITIALIZED SUCCESSFULLY"
    )

except Exception as e:

    print(
        "DATABASE INITIALIZATION ERROR:",
        repr(e)
    )


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