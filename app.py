import os
import base64
import sqlite3
import requests

from flask import Flask, request, jsonify, session, send_file, render_template
from werkzeug.security import generate_password_hash, check_password_hash


app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "my-ai-studio-secret-key-2026"
)

# =========================================================
# CONFIG
# =========================================================

NEW_USER_CREDITS = 100

IMAGE_COST = 5
AUDIO_COST = 8
VIDEO_COST = 21

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "").strip()

POLLINATIONS_BASE = "https://gen.pollinations.ai"

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip().lower()


# =========================================================
# DATABASE
# =========================================================

def use_postgres():
    return bool(DATABASE_URL)


def get_db():
    if use_postgres():
        import psycopg2
        return psycopg2.connect(DATABASE_URL)
    else:
        conn = sqlite3.connect("users.db")
        conn.row_factory = sqlite3.Row
        return conn


def db_execute(query, params=(), fetch=False, many=False):
    conn = get_db()

    try:
        cur = conn.cursor()

        if many:
            cur.executemany(query, params)
        else:
            cur.execute(query, params)

        if fetch:
            rows = cur.fetchall()
            return rows

        conn.commit()

    finally:
        conn.close()


def init_database():
    conn = get_db()

    try:
        cur = conn.cursor()

        if use_postgres():

            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(100) UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    credits INTEGER DEFAULT 100,
                    is_admin BOOLEAN DEFAULT FALSE,
                    is_blocked BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS generation_history (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(100) NOT NULL,
                    generation_type VARCHAR(20) NOT NULL,
                    prompt TEXT,
                    credits_used INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS credit_history (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(100) NOT NULL,
                    amount INTEGER NOT NULL,
                    reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Existing database migration
            try:
                cur.execute("""
                    ALTER TABLE users
                    ADD COLUMN is_admin BOOLEAN DEFAULT FALSE
                """)
            except Exception:
                conn.rollback()

            try:
                cur.execute("""
                    ALTER TABLE users
                    ADD COLUMN is_blocked BOOLEAN DEFAULT FALSE
                """)
            except Exception:
                conn.rollback()

            conn.commit()

        else:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    credits INTEGER DEFAULT 100,
                    is_admin INTEGER DEFAULT 0,
                    is_blocked INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS generation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    generation_type TEXT NOT NULL,
                    prompt TEXT,
                    credits_used INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS credit_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.commit()

        # Make ADMIN_USERNAME admin if account already exists
        if ADMIN_USERNAME:

            if use_postgres():
                cur.execute("""
                    UPDATE users
                    SET is_admin = TRUE
                    WHERE LOWER(username) = %s
                """, (ADMIN_USERNAME,))
            else:
                cur.execute("""
                    UPDATE users
                    SET is_admin = 1
                    WHERE LOWER(username) = ?
                """, (ADMIN_USERNAME,))

            conn.commit()

    finally:
        conn.close()


init_database()


# =========================================================
# HELPERS
# =========================================================

def get_current_user():
    username = session.get("username")

    if not username:
        return None

    if use_postgres():

        rows = db_execute("""
            SELECT id, username, credits, is_admin, is_blocked
            FROM users
            WHERE LOWER(username) = LOWER(%s)
        """, (username,), fetch=True)

    else:

        rows = db_execute("""
            SELECT id, username, credits, is_admin, is_blocked
            FROM users
            WHERE LOWER(username) = LOWER(?)
        """, (username,), fetch=True)

    if not rows:
        return None

    return rows[0]


def login_required():
    user = get_current_user()

    if not user:
        return None

    blocked = user[4] if use_postgres() else user["is_blocked"]

    if blocked:
        session.clear()
        return None

    return user


def admin_required():
    user = login_required()

    if not user:
        return None

    is_admin = user[3] if use_postgres() else user["is_admin"]

    if not is_admin:
        return None

    return user


def get_username(user):
    if use_postgres():
        return user[1]
    return user["username"]


def get_credits(user):
    if use_postgres():
        return user[2]
    return user["credits"]


def get_admin_status(user):
    if use_postgres():
        return bool(user[3])
    return bool(user["is_admin"])


def add_credit_history(username, amount, reason):
    if use_postgres():

        db_execute("""
            INSERT INTO credit_history
            (username, amount, reason)
            VALUES (%s, %s, %s)
        """, (username, amount, reason))

    else:

        db_execute("""
            INSERT INTO credit_history
            (username, amount, reason)
            VALUES (?, ?, ?)
        """, (username, amount, reason))


def add_generation_history(username, generation_type, prompt, credits):
    if use_postgres():

        db_execute("""
            INSERT INTO generation_history
            (username, generation_type, prompt, credits_used)
            VALUES (%s, %s, %s, %s)
        """, (
            username,
            generation_type,
            prompt,
            credits
        ))

    else:

        db_execute("""
            INSERT INTO generation_history
            (username, generation_type, prompt, credits_used)
            VALUES (?, ?, ?, ?)
        """, (
            username,
            generation_type,
            prompt,
            credits
        ))


def deduct_credits(username, amount):

    if use_postgres():

        db_execute("""
            UPDATE users
            SET credits = credits - %s
            WHERE LOWER(username) = LOWER(%s)
              AND credits >= %s
        """, (amount, username, amount))

    else:

        db_execute("""
            UPDATE users
            SET credits = credits - ?
            WHERE LOWER(username) = LOWER(?)
              AND credits >= ?
        """, (amount, username, amount))


def get_user_by_username(username):

    if use_postgres():

        rows = db_execute("""
            SELECT id, username, credits, is_admin, is_blocked
            FROM users
            WHERE LOWER(username) = LOWER(%s)
        """, (username,), fetch=True)

    else:

        rows = db_execute("""
            SELECT id, username, credits, is_admin, is_blocked
            FROM users
            WHERE LOWER(username) = LOWER(?)
        """, (username,), fetch=True)

    if not rows:
        return None

    return rows[0]


# =========================================================
# FRONT PAGE
# =========================================================

@app.route("/")
def home():
    return render_template("index.html")


# =========================================================
# REGISTER
# =========================================================

@app.route("/register", methods=["POST"])
def register():

    data = request.get_json() or {}

    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))

    if len(username) < 3:
        return jsonify({
            "error": "Username must be at least 3 characters"
        }), 400

    if len(password) < 6:
        return jsonify({
            "error": "Password must be at least 6 characters"
        }), 400

    existing = get_user_by_username(username)

    if existing:
        return jsonify({
            "error": "Username already exists"
        }), 400

    password_hash = generate_password_hash(password)

    is_admin = (
        bool(ADMIN_USERNAME)
        and username.lower() == ADMIN_USERNAME
    )

    if use_postgres():

        db_execute("""
            INSERT INTO users
            (username, password, credits, is_admin, is_blocked)
            VALUES (%s, %s, %s, %s, FALSE)
        """, (
            username,
            password_hash,
            NEW_USER_CREDITS,
            is_admin
        ))

    else:

        db_execute("""
            INSERT INTO users
            (username, password, credits, is_admin, is_blocked)
            VALUES (?, ?, ?, ?, 0)
        """, (
            username,
            password_hash,
            NEW_USER_CREDITS,
            1 if is_admin else 0
        ))

    add_credit_history(
        username,
        NEW_USER_CREDITS,
        "Welcome bonus"
    )

    return jsonify({
        "success": True,
        "message": "Account created successfully"
    })


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["POST"])
def login():

    data = request.get_json() or {}

    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))

    if use_postgres():

        rows = db_execute("""
            SELECT id, username, password, credits,
                   is_admin, is_blocked
            FROM users
            WHERE LOWER(username) = LOWER(%s)
        """, (username,), fetch=True)

    else:

        rows = db_execute("""
            SELECT id, username, password, credits,
                   is_admin, is_blocked
            FROM users
            WHERE LOWER(username) = LOWER(?)
        """, (username,), fetch=True)

    if not rows:
        return jsonify({
            "error": "Invalid username or password"
        }), 401

    user = rows[0]

    if use_postgres():

        stored_password = user[2]
        blocked = user[5]

    else:

        stored_password = user["password"]
        blocked = user["is_blocked"]

    if blocked:
        return jsonify({
            "error": "This account has been blocked"
        }), 403

    if not check_password_hash(stored_password, password):
        return jsonify({
            "error": "Invalid username or password"
        }), 401

    session["username"] = username

    return jsonify({
        "success": True,
        "username": username,
        "is_admin": (
            bool(user[4])
            if use_postgres()
            else bool(user["is_admin"])
        ),
        "credits": (
            user[3]
            if use_postgres()
            else user["credits"]
        )
    })


# =========================================================
# CURRENT USER
# =========================================================

@app.route("/me")
def me():

    user = login_required()

    if not user:
        return jsonify({
            "logged_in": False
        })

    return jsonify({
        "logged_in": True,
        "username": get_username(user),
        "credits": get_credits(user),
        "is_admin": get_admin_status(user)
    })


# =========================================================
# CREDITS
# =========================================================

@app.route("/credits")
def credits():

    user = login_required()

    if not user:
        return jsonify({
            "error": "Not logged in"
        }), 401

    return jsonify({
        "credits": get_credits(user)
    })


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
# IMAGE GENERATION
# =========================================================

@app.route("/generate", methods=["POST"])
def generate():

    user = login_required()

    if not user:
        return jsonify({
            "error": "Please login first"
        }), 401

    data = request.get_json() or {}

    prompt = str(data.get("prompt", "")).strip()

    if not prompt:
        return jsonify({
            "error": "Please enter a prompt"
        }), 400

    current_credits = get_credits(user)

    if current_credits < IMAGE_COST:
        return jsonify({
            "error": "Not enough credits"
        }), 402

    if not POLLINATIONS_API_KEY:
        return jsonify({
            "error": "Pollinations API key is not configured"
        }), 500

    try:

        response = requests.post(
            f"{POLLINATIONS_BASE}/v1/images/generations",
            headers={
                "Authorization": f"Bearer {POLLINATIONS_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "black-forest-labs/flux.1-schnell",
                "prompt": prompt,
                "size": "1024x1024",
                "n": 1,
                "response_format": "url"
            },
            timeout=180
        )

        if response.status_code != 200:

            return jsonify({
                "error": f"Image generation failed: {response.text[:500]}"
            }), 500

        result = response.json()

        image_url = None

        if result.get("data"):

            first = result["data"][0]

            image_url = first.get("url")

            if first.get("b64_json"):

                image_bytes = base64.b64decode(
                    first["b64_json"]
                )

                with open("generated.png", "wb") as f:
                    f.write(image_bytes)

                image_url = "/generated.png"

        if not image_url:

            return jsonify({
                "error": "Image URL was not returned"
            }), 500

        deduct_credits(
            get_username(user),
            IMAGE_COST
        )

        add_generation_history(
            get_username(user),
            "image",
            prompt,
            IMAGE_COST
        )

        add_credit_history(
            get_username(user),
            -IMAGE_COST,
            "Image generation"
        )

        return jsonify({
            "success": True,
            "image_url": image_url,
            "credits": current_credits - IMAGE_COST
        })

    except Exception as e:

        print("IMAGE ERROR:", str(e))

        return jsonify({
            "error": str(e)
        }), 500


@app.route("/generated.png")
def generated_image():

    if not os.path.exists("generated.png"):
        return jsonify({
            "error": "No image available"
        }), 404

    return send_file(
        "generated.png",
        mimetype="image/png"
    )


# =========================================================
# AUDIO GENERATION
# =========================================================

@app.route("/audio", methods=["POST"])
def audio():

    user = login_required()

    if not user:
        return jsonify({
            "error": "Please login first"
        }), 401

    data = request.get_json() or {}

    text = str(data.get("text", "")).strip()

    if not text:
        return jsonify({
            "error": "Please enter text"
        }), 400

    current_credits = get_credits(user)

    if current_credits < AUDIO_COST:
        return jsonify({
            "error": "Not enough credits"
        }), 402

    if not POLLINATIONS_API_KEY:
        return jsonify({
            "error": "Pollinations API key is not configured"
        }), 500

    try:

        response = requests.post(
            f"{POLLINATIONS_BASE}/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {POLLINATIONS_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "elevenlabs/eleven-v3",
                "input": text,
                "voice": "nova",
                "response_format": "wav"
            },
            timeout=180
        )

        if response.status_code != 200:

            return jsonify({
                "error": f"Audio generation failed: {response.text[:500]}"
            }), 500

        with open("generated.wav", "wb") as f:
            f.write(response.content)

        deduct_credits(
            get_username(user),
            AUDIO_COST
        )

        add_generation_history(
            get_username(user),
            "audio",
            text,
            AUDIO_COST
        )

        add_credit_history(
            get_username(user),
            -AUDIO_COST,
            "Audio generation"
        )

        return jsonify({
            "success": True,
            "audio_url": "/generated.wav",
            "credits": current_credits - AUDIO_COST
        })

    except Exception as e:

        print("AUDIO ERROR:", str(e))

        return jsonify({
            "error": str(e)
        }), 500


@app.route("/generated.wav")
def generated_audio():

    if not os.path.exists("generated.wav"):
        return jsonify({
            "error": "No audio available"
        }), 404

    return send_file(
        "generated.wav",
        mimetype="audio/wav"
    )


# =========================================================
# VIDEO GENERATION
# =========================================================

@app.route("/video", methods=["POST"])
def video():

    user = login_required()

    if not user:
        return jsonify({
            "error": "Please login first"
        }), 401

    data = request.get_json() or {}

    prompt = str(data.get("prompt", "")).strip()

    if not prompt:
        return jsonify({
            "error": "Please enter a prompt"
        }), 400

    current_credits = get_credits(user)

    if current_credits < VIDEO_COST:
        return jsonify({
            "error": "Not enough credits"
        }), 402

    if not POLLINATIONS_API_KEY:
        return jsonify({
            "error": "Pollinations API key is not configured"
        }), 500

    try:

        from urllib.parse import quote

        encoded_prompt = quote(
            prompt,
            safe=""
        )

        response = requests.get(
            f"{POLLINATIONS_BASE}/video/{encoded_prompt}",
            headers={
                "Authorization": f"Bearer {POLLINATIONS_API_KEY}"
            },
            params={
                "model": "google/veo-3.1-fast",
                "duration": 4,
                "aspectRatio": "16:9",
                "audio": "false"
            },
            timeout=600
        )

        if response.status_code != 200:

            return jsonify({
                "error": f"Video generation failed: {response.text[:500]}"
            }), 500

        content_type = response.headers.get(
            "Content-Type",
            ""
        )

        if "video" not in content_type.lower():

            return jsonify({
                "error": "Video was not returned by the AI service"
            }), 500

        with open("generated.mp4", "wb") as f:
            f.write(response.content)

        deduct_credits(
            get_username(user),
            VIDEO_COST
        )

        add_generation_history(
            get_username(user),
            "video",
            prompt,
            VIDEO_COST
        )

        add_credit_history(
            get_username(user),
            -VIDEO_COST,
            "Video generation"
        )

        return jsonify({
            "success": True,
            "video_url": "/generated.mp4",
            "credits": current_credits - VIDEO_COST
        })

    except Exception as e:

        print("VIDEO ERROR:", str(e))

        return jsonify({
            "error": str(e)
        }), 500


@app.route("/generated.mp4")
def generated_video():

    if not os.path.exists("generated.mp4"):
        return jsonify({
            "error": "No video available"
        }), 404

    return send_file(
        "generated.mp4",
        mimetype="video/mp4"
    )


# =========================================================
# ADMIN - USERS
# =========================================================

@app.route("/admin/users")
def admin_users():

    admin = admin_required()

    if not admin:
        return jsonify({
            "error": "Admin access required"
        }), 403

    if use_postgres():

        rows = db_execute("""
            SELECT id, username, credits,
                   is_admin, is_blocked, created_at
            FROM users
            ORDER BY id DESC
        """, fetch=True)

        users = []

        for row in rows:

            users.append({
                "id": row[0],
                "username": row[1],
                "credits": row[2],
                "is_admin": bool(row[3]),
                "is_blocked": bool(row[4]),
                "created_at": str(row[5])
            })

    else:

        rows = db_execute("""
            SELECT id, username, credits,
                   is_admin, is_blocked, created_at
            FROM users
            ORDER BY id DESC
        """, fetch=True)

        users = []

        for row in rows:

            users.append({
                "id": row["id"],
                "username": row["username"],
                "credits": row["credits"],
                "is_admin": bool(row["is_admin"]),
                "is_blocked": bool(row["is_blocked"]),
                "created_at": str(row["created_at"])
            })

    return jsonify({
        "users": users
    })


# =========================================================
# ADMIN - ADD / REMOVE CREDITS
# =========================================================

@app.route("/admin/credits", methods=["POST"])
def admin_credits():

    admin = admin_required()

    if not admin:
        return jsonify({
            "error": "Admin access required"
        }), 403

    data = request.get_json() or {}

    username = str(
        data.get("username", "")
    ).strip()

    try:
        amount = int(data.get("amount", 0))
    except:
        amount = 0

    if not username:
        return jsonify({
            "error": "Username required"
        }), 400

    if amount == 0:
        return jsonify({
            "error": "Amount cannot be zero"
        }), 400

    target = get_user_by_username(username)

    if not target:
        return jsonify({
            "error": "User not found"
        }), 404

    if use_postgres():

        db_execute("""
            UPDATE users
            SET credits = GREATEST(0, credits + %s)
            WHERE LOWER(username) = LOWER(%s)
        """, (amount, username))

    else:

        db_execute("""
            UPDATE users
            SET credits = MAX(0, credits + ?)
            WHERE LOWER(username) = LOWER(?)
        """, (amount, username))

    add_credit_history(
        username,
        amount,
        "Admin credit adjustment"
    )

    return jsonify({
        "success": True
    })


# =========================================================
# ADMIN - BLOCK / UNBLOCK
# =========================================================

@app.route("/admin/block", methods=["POST"])
def admin_block():

    admin = admin_required()

    if not admin:
        return jsonify({
            "error": "Admin access required"
        }), 403

    data = request.get_json() or {}

    username = str(
        data.get("username", "")
    ).strip()

    target = get_user_by_username(username)

    if not target:
        return jsonify({
            "error": "User not found"
        }), 404

    if username.lower() == get_username(admin).lower():
        return jsonify({
            "error": "You cannot block yourself"
        }), 400

    if use_postgres():

        current_blocked = bool(target[4])

        db_execute("""
            UPDATE users
            SET is_blocked = %s
            WHERE LOWER(username) = LOWER(%s)
        """, (
            not current_blocked,
            username
        ))

    else:

        current_blocked = bool(target["is_blocked"])

        db_execute("""
            UPDATE users
            SET is_blocked = ?
            WHERE LOWER(username) = LOWER(?)
        """, (
            0 if current_blocked else 1,
            username
        ))

    return jsonify({
        "success": True,
        "blocked": not current_blocked
    })


# =========================================================
# ADMIN - GENERATION HISTORY
# =========================================================

@app.route("/admin/generations")
def admin_generations():

    admin = admin_required()

    if not admin:
        return jsonify({
            "error": "Admin access required"
        }), 403

    if use_postgres():

        rows = db_execute("""
            SELECT id, username, generation_type,
                   prompt, credits_used, created_at
            FROM generation_history
            ORDER BY id DESC
            LIMIT 200
        """, fetch=True)

        history = [
            {
                "id": r[0],
                "username": r[1],
                "type": r[2],
                "prompt": r[3],
                "credits": r[4],
                "created_at": str(r[5])
            }
            for r in rows
        ]

    else:

        rows = db_execute("""
            SELECT id, username, generation_type,
                   prompt, credits_used, created_at
            FROM generation_history
            ORDER BY id DESC
            LIMIT 200
        """, fetch=True)

        history = [
            {
                "id": r["id"],
                "username": r["username"],
                "type": r["generation_type"],
                "prompt": r["prompt"],
                "credits": r["credits_used"],
                "created_at": str(r["created_at"])
            }
            for r in rows
        ]

    return jsonify({
        "history": history
    })


# =========================================================
# ADMIN - CREDIT HISTORY
# =========================================================

@app.route("/admin/credit-history")
def admin_credit_history():

    admin = admin_required()

    if not admin:
        return jsonify({
            "error": "Admin access required"
        }), 403

    if use_postgres():

        rows = db_execute("""
            SELECT id, username, amount,
                   reason, created_at
            FROM credit_history
            ORDER BY id DESC
            LIMIT 200
        """, fetch=True)

        history = [
            {
                "id": r[0],
                "username": r[1],
                "amount": r[2],
                "reason": r[3],
                "created_at": str(r[4])
            }
            for r in rows
        ]

    else:

        rows = db_execute("""
            SELECT id, username, amount,
                   reason, created_at
            FROM credit_history
            ORDER BY id DESC
            LIMIT 200
        """, fetch=True)

        history = [
            {
                "id": r["id"],
                "username": r["username"],
                "amount": r["amount"],
                "reason": r["reason"],
                "created_at": str(r["created_at"])
            }
            for r in rows
        ]

    return jsonify({
        "history": history
    })


# =========================================================
# ADMIN - STATS
# =========================================================

@app.route("/admin/stats")
def admin_stats():

    admin = admin_required()

    if not admin:
        return jsonify({
            "error": "Admin access required"
        }), 403

    if use_postgres():

        user_count = db_execute(
            "SELECT COUNT(*) FROM users",
            fetch=True
        )[0][0]

        total_generations = db_execute(
            "SELECT COUNT(*) FROM generation_history",
            fetch=True
        )[0][0]

        total_credits = db_execute(
            "SELECT COALESCE(SUM(credits), 0) FROM users",
            fetch=True
        )[0][0]

        blocked_users = db_execute(
            "SELECT COUNT(*) FROM users WHERE is_blocked = TRUE",
            fetch=True
        )[0][0]

    else:

        user_count = db_execute(
            "SELECT COUNT(*) FROM users",
            fetch=True
        )[0][0]

        total_generations = db_execute(
            "SELECT COUNT(*) FROM generation_history",
            fetch=True
        )[0][0]

        total_credits = db_execute(
            "SELECT COALESCE(SUM(credits), 0) FROM users",
            fetch=True
        )[0][0]

        blocked_users = db_execute(
            "SELECT COUNT(*) FROM users WHERE is_blocked = 1",
            fetch=True
        )[0][0]

    return jsonify({
        "users": user_count,
        "generations": total_generations,
        "credits": total_credits,
        "blocked": blocked_users
    })


# =========================================================
# HEALTH
# =========================================================

@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "database": "postgresql" if use_postgres() else "sqlite",
        "pollinations_configured": bool(POLLINATIONS_API_KEY)
    })


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    port = int(
        os.getenv("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
    