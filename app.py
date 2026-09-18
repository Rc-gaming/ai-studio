import os
import time
import base64
import sqlite3
import threading
import requests

from flask import Flask, request, jsonify, send_file
from flask import session
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "my-ai-studio-secret-key-2026"
)

# =========================
# SETTINGS
# =========================

NEW_USER_CREDITS = 100
IMAGE_COST = 5
AUDIO_COST = 8
VIDEO_COST = 21

DATABASE_URL = os.getenv("DATABASE_URL")
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY")

POLLINATIONS_BASE = "https://gen.pollinations.ai"

# =========================
# DATABASE
# =========================

def is_postgres():
    return bool(DATABASE_URL)


def db():
    if is_postgres():
        import psycopg2
        return psycopg2.connect(DATABASE_URL)

    return sqlite3.connect("users.db")


def db_placeholder():
    if is_postgres():
        return "%s"

    return "?"


def init_database():
    con = db()
    cur = con.cursor()

    if is_postgres():
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                credits INTEGER NOT NULL DEFAULT 100
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                credits INTEGER NOT NULL DEFAULT 100
            )
        """)

    con.commit()
    cur.close()
    con.close()


init_database()


# =========================
# CREDIT FUNCTIONS
# =========================

def get_credits(user_id):
    con = db()
    cur = con.cursor()
    p = db_placeholder()

    cur.execute(
        f"SELECT credits FROM users WHERE id={p}",
        (user_id,)
    )

    row = cur.fetchone()

    cur.close()
    con.close()

    return row[0] if row else 0


def add_credits(user_id, amount):
    con = db()
    cur = con.cursor()
    p = db_placeholder()

    cur.execute(
        f"UPDATE users SET credits=credits+{p} WHERE id={p}",
        (amount, user_id)
    )

    con.commit()
    cur.close()
    con.close()


def use_credits(user_id, amount):
    con = db()
    cur = con.cursor()
    p = db_placeholder()

    cur.execute(
        f"""
        UPDATE users
        SET credits=credits-{p}
        WHERE id={p} AND credits>={p}
        """,
        (amount, user_id, amount)
    )

    success = cur.rowcount == 1

    con.commit()
    cur.close()
    con.close()

    return success


# =========================
# AUTH
# =========================

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({
                "error": "Please login first"
            }), 401

        return fn(*args, **kwargs)

    return wrapper


# =========================
# HOME
# =========================

@app.route("/")
def home():
    return send_file(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "index.html"
        )
    )


# =========================
# REGISTER
# =========================

@app.route("/register", methods=["POST"])
def register():
    try:
        data = request.get_json() or {}

        username = data.get("username", "").strip()
        password = data.get("password", "")

        if not username or not password:
            return jsonify({
                "error": "Username and password are required"
            }), 400

        if len(password) < 6:
            return jsonify({
                "error": "Password must be at least 6 characters"
            }), 400

        con = db()
        cur = con.cursor()
        p = db_placeholder()

        cur.execute(
            f"""
            INSERT INTO users(username,password,credits)
            VALUES({p},{p},{p})
            """,
            (
                username,
                generate_password_hash(password),
                NEW_USER_CREDITS
            )
        )

        con.commit()

        cur.close()
        con.close()

        return jsonify({
            "message": "Account created successfully",
            "credits": NEW_USER_CREDITS
        })

    except Exception as e:
        print("REGISTER ERROR:", str(e))

        return jsonify({
            "error": "Username already exists or registration failed"
        }), 400


# =========================
# LOGIN
# =========================

@app.route("/login", methods=["POST"])
def login():
    try:
        data = request.get_json() or {}

        username = data.get("username", "").strip()
        password = data.get("password", "")

        if not username or not password:
            return jsonify({
                "error": "Username and password are required"
            }), 400

        con = db()
        cur = con.cursor()
        p = db_placeholder()

        cur.execute(
            f"""
            SELECT id, username, password, credits
            FROM users
            WHERE username={p}
            """,
            (username,)
        )

        user = cur.fetchone()

        cur.close()
        con.close()

        if not user:
            return jsonify({
                "error": "Username or password is incorrect"
            }), 401

        if not check_password_hash(user[2], password):
            return jsonify({
                "error": "Username or password is incorrect"
            }), 401

        session["user_id"] = user[0]
        session["username"] = user[1]

        return jsonify({
            "message": "Login successful",
            "username": user[1],
            "credits": user[3]
        })

    except Exception as e:
        print("LOGIN ERROR:", str(e))

        return jsonify({
            "error": "Login failed"
        }), 500


# =========================
# ME
# =========================

@app.route("/me")
def me():
    if "user_id" in session:
        return jsonify({
            "logged_in": True,
            "username": session["username"],
            "credits": get_credits(session["user_id"])
        })

    return jsonify({
        "logged_in": False
    })


# =========================
# CREDITS
# =========================

@app.route("/credits")
@login_required
def credits():
    return jsonify({
        "credits": get_credits(session["user_id"])
    })


# =========================
# LOGOUT
# =========================

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()

    return jsonify({
        "message": "Logged out successfully"
    })
# =========================
# POLLINATIONS HELPERS
# =========================

def pollinations_headers():
    if not POLLINATIONS_API_KEY:
        raise RuntimeError(
            "POLLINATIONS_API_KEY is not configured on the server."
        )

    return {
        "Authorization": f"Bearer {POLLINATIONS_API_KEY}",
        "Content-Type": "application/json"
    }


def pollinations_error(response):
    try:
        data = response.json()

        if isinstance(data, dict):
            if data.get("error"):
                return str(data["error"])

            if data.get("message"):
                return str(data["message"])

        return response.text[:500]

    except Exception:
        return response.text[:500]


# =========================
# IMAGE GENERATION
# =========================

@app.route("/generate", methods=["POST"])
@login_required
def generate():
    try:
        data = request.get_json() or {}

        prompt = data.get("prompt", "").strip()

        if not prompt:
            return jsonify({
                "error": "Please enter a prompt"
            }), 400

        user_id = session["user_id"]

        current_credits = get_credits(user_id)

        if current_credits < IMAGE_COST:
            return jsonify({
                "error": "Your credits are finished. Please recharge to continue."
            }), 402

        headers = pollinations_headers()

        payload = {
            "prompt": prompt,
            "model": "black-forest-labs/flux.1-schnell",
            "size": "1024x1024",
            "n": 1,
            "response_format": "url"
        }

        print("IMAGE: Sending request to Pollinations...")

        response = requests.post(
            f"{POLLINATIONS_BASE}/v1/images/generations",
            headers=headers,
            json=payload,
            timeout=300
        )

        if response.status_code != 200:
            error_message = pollinations_error(response)

            print(
                "IMAGE POLLINATIONS ERROR:",
                response.status_code,
                error_message
            )

            return jsonify({
                "error": f"Image generation failed: {error_message}"
            }), response.status_code

        result = response.json()

        image_url = None

        if isinstance(result, dict):
            data_list = result.get("data", [])

            if data_list and isinstance(data_list[0], dict):
                image_url = data_list[0].get("url")

                # Backup if API returns base64
                b64_data = data_list[0].get("b64_json")

                if not image_url and b64_data:
                    image_bytes = base64.b64decode(b64_data)

                    with open("generated.png", "wb") as f:
                        f.write(image_bytes)

                    image_url = "/generated.png"

        if not image_url:
            return jsonify({
                "error": "Image was generated but no image URL was returned."
            }), 500

        # Deduct credits only after successful generation
        if not use_credits(user_id, IMAGE_COST):
            return jsonify({
                "error": "Unable to deduct credits."
            }), 500

        print("IMAGE: Success")

        return jsonify({
            "image_url": image_url,
            "credits_used": IMAGE_COST,
            "credits_remaining": get_credits(user_id)
        })

    except requests.Timeout:
        print("IMAGE ERROR: Request timed out")

        return jsonify({
            "error": "Image generation timed out. Please try again."
        }), 504

    except Exception as e:
        print("IMAGE ERROR:", str(e))

        return jsonify({
            "error": str(e)
        }), 500


# =========================
# LOCAL GENERATED IMAGE
# =========================

@app.route("/generated.png")
def generated_image():
    file_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "generated.png"
    )

    if not os.path.exists(file_path):
        return jsonify({
            "error": "Generated image not found"
        }), 404

    return send_file(
        file_path,
        mimetype="image/png"
    )


# =========================
# AUDIO GENERATION
# =========================

@app.route("/audio", methods=["POST"])
@login_required
def audio():
    try:
        data = request.get_json() or {}

        text = data.get("text", "").strip()

        if not text:
            return jsonify({
                "error": "Please enter text"
            }), 400

        user_id = session["user_id"]

        current_credits = get_credits(user_id)

        if current_credits < AUDIO_COST:
            return jsonify({
                "error": "Your credits are finished. Please recharge to continue."
            }), 402

        headers = pollinations_headers()

        payload = {
            "model": "elevenlabs/eleven-v3",
            "input": text,
            "voice": "nova",
            "response_format": "wav"
        }

        print("AUDIO: Sending request to Pollinations...")

        response = requests.post(
            f"{POLLINATIONS_BASE}/v1/audio/speech",
            headers=headers,
            json=payload,
            timeout=300
        )

        if response.status_code != 200:
            error_message = pollinations_error(response)

            print(
                "AUDIO POLLINATIONS ERROR:",
                response.status_code,
                error_message
            )

            return jsonify({
                "error": f"Audio generation failed: {error_message}"
            }), response.status_code

        audio_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "generated.wav"
        )

        with open(audio_path, "wb") as f:
            f.write(response.content)

        # Make sure something was actually returned
        if os.path.getsize(audio_path) == 0:
            return jsonify({
                "error": "Audio generation returned an empty file."
            }), 500

        # Deduct only after successful generation
        if not use_credits(user_id, AUDIO_COST):
            return jsonify({
                "error": "Unable to deduct credits."
            }), 500

        print("AUDIO: Success")

        return jsonify({
            "audio_url": "/generated.wav",
            "credits_used": AUDIO_COST,
            "credits_remaining": get_credits(user_id)
        })

    except requests.Timeout:
        print("AUDIO ERROR: Request timed out")

        return jsonify({
            "error": "Audio generation timed out. Please try again."
        }), 504

    except Exception as e:
        print("AUDIO ERROR:", str(e))

        return jsonify({
            "error": str(e)
        }), 500


# =========================
# LOCAL GENERATED AUDIO
# =========================

@app.route("/generated.wav")
def generated_audio():
    file_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "generated.wav"
    )

    if not os.path.exists(file_path):
        return jsonify({
            "error": "Generated audio not found"
        }), 404

    return send_file(
        file_path,
        mimetype="audio/wav"
    )
# =========================
# VIDEO GENERATION
# =========================

@app.route("/video", methods=["POST"])
@login_required
def video():
    try:
        data = request.get_json() or {}

        prompt = data.get("prompt", "").strip()

        if not prompt:
            return jsonify({
                "error": "Please enter a prompt"
            }), 400

        user_id = session["user_id"]

        current_credits = get_credits(user_id)

        if current_credits < VIDEO_COST:
            return jsonify({
                "error": "Your credits are finished. Please recharge to continue."
            }), 402

        if not POLLINATIONS_API_KEY:
            return jsonify({
                "error": "POLLINATIONS_API_KEY is not configured."
            }), 500

        # Pollinations video endpoint
        video_url = f"{POLLINATIONS_BASE}/video/{requests.utils.quote(prompt, safe='')}"

        params = {
            "model": "google/veo-3.1-fast",
            "duration": 4,
            "aspectRatio": "16:9",
            "audio": False
        }

        headers = {
            "Authorization": f"Bearer {POLLINATIONS_API_KEY}"
        }

        print("VIDEO: Sending request to Pollinations...")
        print("VIDEO PROMPT:", prompt)

        response = requests.get(
            video_url,
            headers=headers,
            params=params,
            timeout=600
        )

        if response.status_code != 200:
            error_message = pollinations_error(response)

            print(
                "VIDEO POLLINATIONS ERROR:",
                response.status_code,
                error_message
            )

            return jsonify({
                "error": f"Video generation failed: {error_message}"
            }), response.status_code

        video_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "generated.mp4"
        )

        with open(video_path, "wb") as f:
            f.write(response.content)

        if os.path.getsize(video_path) == 0:
            return jsonify({
                "error": "Video generation returned an empty file."
            }), 500

        # Deduct only after successful generation
        if not use_credits(user_id, VIDEO_COST):
            return jsonify({
                "error": "Unable to deduct credits."
            }), 500

        print("VIDEO: Success")

        return jsonify({
            "video_url": "/generated.mp4",
            "credits_used": VIDEO_COST,
            "credits_remaining": get_credits(user_id)
        })

    except requests.Timeout:
        print("VIDEO ERROR: Request timed out")

        return jsonify({
            "error": "Video generation timed out. Please try again."
        }), 504

    except Exception as e:
        print("VIDEO ERROR:", str(e))

        return jsonify({
            "error": str(e)
        }), 500


# =========================
# LOCAL GENERATED VIDEO
# =========================

@app.route("/generated.mp4")
def generated_video():
    file_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "generated.mp4"
    )

    if not os.path.exists(file_path):
        return jsonify({
            "error": "Generated video not found"
        }), 404

    return send_file(
        file_path,
        mimetype="video/mp4"
    )


# =========================
# RECHARGE
# =========================

@app.route("/recharge", methods=["POST"])
@login_required
def recharge():
    return jsonify({
        "error": "Recharge is currently unavailable. Payment system is not connected yet."
    }), 403


# =========================
# HEALTH CHECK
# =========================

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "database": "postgres" if DATABASE_URL else "sqlite",
        "pollinations_configured": bool(POLLINATIONS_API_KEY)
    })


# =========================
# START SERVER
# =========================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        debug=False
    )