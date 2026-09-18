import os
import io
import json
import time
import base64
import sqlite3
import threading
import wave

import requests

from flask import Flask, request, jsonify, send_file, session
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps


# =========================================================
# APP CONFIG
# =========================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "my-ai-studio-secret-key-2026"
)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Current Google models
IMAGE_MODEL = "gemini-3.1-flash-image"
AUDIO_MODEL = "gemini-3.1-flash-tts-preview"
VIDEO_MODEL = "veo-3.1-fast-generate-preview"

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

DATABASE = "users.db"

# Credits
STARTING_CREDITS = 100
IMAGE_COST = 5
AUDIO_COST = 8
VIDEO_COST = 21
GENERATE_ALL_COST = 34


# =========================================================
# VIDEO JOB STORAGE
# =========================================================

video_jobs = {}


# =========================================================
# DATABASE
# =========================================================

def get_db():
    """
    PostgreSQL on Render.
    SQLite locally if DATABASE_URL is not available.
    """

    if DATABASE_URL:
        import psycopg2

        return psycopg2.connect(DATABASE_URL)

    conn = sqlite3.connect(
        DATABASE,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def is_postgres():
    return bool(DATABASE_URL)


def db_execute(query, params=(), fetchone=False, fetchall=False):
    """
    Small database helper that supports both
    PostgreSQL and SQLite.
    """

    conn = get_db()

    try:
        if is_postgres():
            query = query.replace("?", "%s")

        cur = conn.cursor()
        cur.execute(query, params)

        result = None

        if fetchone:
            row = cur.fetchone()
            if row is not None:
                if is_postgres():
                    columns = [desc[0] for desc in cur.description]
                    result = dict(zip(columns, row))
                else:
                    result = dict(row)

        elif fetchall:
            rows = cur.fetchall()

            if is_postgres():
                columns = [desc[0] for desc in cur.description]
                result = [
                    dict(zip(columns, row))
                    for row in rows
                ]
            else:
                result = [dict(row) for row in rows]

        conn.commit()
        return result

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def init_db():
    """
    Create users table and credits column.
    """

    conn = get_db()

    try:
        cur = conn.cursor()

        if is_postgres():

            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    credits INTEGER NOT NULL DEFAULT 100
                )
            """)

            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS credits INTEGER
                NOT NULL DEFAULT 100
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

            # Migration for an older SQLite database
            cur.execute("PRAGMA table_info(users)")
            columns = [row[1] for row in cur.fetchall()]

            if "credits" not in columns:
                cur.execute("""
                    ALTER TABLE users
                    ADD COLUMN credits INTEGER NOT NULL DEFAULT 100
                """)

        conn.commit()

    finally:
        conn.close()


init_db()


# =========================================================
# AUTH HELPERS
# =========================================================

def login_required(func):

    @wraps(func)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:
            return jsonify({
                "error": "Please login first"
            }), 401

        return func(*args, **kwargs)

    return wrapper


def current_user():
    user_id = session.get("user_id")

    if not user_id:
        return None

    return db_execute(
        """
        SELECT id, username, credits
        FROM users
        WHERE id = ?
        """,
        (user_id,),
        fetchone=True
    )


# =========================================================
# CREDIT FUNCTIONS
# =========================================================

def get_user_credits(user_id):

    user = db_execute(
        """
        SELECT credits
        FROM users
        WHERE id = ?
        """,
        (user_id,),
        fetchone=True
    )

    if not user:
        return 0

    return int(user["credits"])


def use_credits(user_id, amount):

    conn = get_db()

    try:

        if is_postgres():

            cur = conn.cursor()

            cur.execute(
                """
                SELECT credits
                FROM users
                WHERE id = %s
                FOR UPDATE
                """,
                (user_id,)
            )

            row = cur.fetchone()

            if not row:
                conn.rollback()
                return False, 0

            current = int(row[0])

            if current < amount:
                conn.rollback()
                return False, current

            new_balance = current - amount

            cur.execute(
                """
                UPDATE users
                SET credits = %s
                WHERE id = %s
                """,
                (new_balance, user_id)
            )

        else:

            cur = conn.cursor()

            cur.execute(
                """
                SELECT credits
                FROM users
                WHERE id = ?
                """,
                (user_id,)
            )

            row = cur.fetchone()

            if not row:
                conn.rollback()
                return False, 0

            current = int(row[0])

            if current < amount:
                conn.rollback()
                return False, current

            new_balance = current - amount

            cur.execute(
                """
                UPDATE users
                SET credits = ?
                WHERE id = ?
                """,
                (new_balance, user_id)
            )

        conn.commit()

        return True, new_balance

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


# =========================================================
# GEMINI REQUEST HELPER
# =========================================================

def gemini_headers():

    return {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json"
    }


def check_gemini_key():

    if not GEMINI_API_KEY:
        return False, "GEMINI_API_KEY is not configured on the server."

    return True, None


# =========================================================
# BASIC ROUTES
# =========================================================

@app.route("/")
def home():

    return app.send_static_file("index.html")


@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "database": "postgresql" if DATABASE_URL else "sqlite",
        "gemini_configured": bool(GEMINI_API_KEY),
        "image_model": IMAGE_MODEL,
        "audio_model": AUDIO_MODEL,
        "video_model": VIDEO_MODEL
    })


# =========================================================
# REGISTER
# =========================================================

@app.route("/register", methods=["POST"])
def register():

    try:

        data = request.get_json(silent=True) or {}

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

        existing = db_execute(
            """
            SELECT id
            FROM users
            WHERE username = ?
            """,
            (username,),
            fetchone=True
        )

        if existing:
            return jsonify({
                "error": "Username already exists."
            }), 409

        hashed_password = generate_password_hash(password)

        db_execute(
            """
            INSERT INTO users
            (username, password, credits)
            VALUES (?, ?, ?)
            """,
            (
                username,
                hashed_password,
                STARTING_CREDITS
            )
        )

        return jsonify({
            "success": True,
            "message": "Registration successful.",
            "credits": STARTING_CREDITS
        })

    except Exception as e:

        print("REGISTER ERROR:", str(e))

        return jsonify({
            "error": "Registration failed."
        }), 500


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["POST"])
def login():

    try:

        data = request.get_json(silent=True) or {}

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

        user = db_execute(
            """
            SELECT id, username, password, credits
            FROM users
            WHERE username = ?
            """,
            (username,),
            fetchone=True
        )

        if not user:

            return jsonify({
                "error": "Invalid username or password."
            }), 401

        if not check_password_hash(
            user["password"],
            password
        ):

            return jsonify({
                "error": "Invalid username or password."
            }), 401

        session["user_id"] = user["id"]
        session["username"] = user["username"]

        return jsonify({
            "success": True,
            "username": user["username"],
            "credits": int(user["credits"])
        })

    except Exception as e:

        print("LOGIN ERROR:", str(e))

        return jsonify({
            "error": "Login failed."
        }), 500


# =========================================================
# ME
# =========================================================

@app.route("/me")
def me():

    user = current_user()

    if not user:

        return jsonify({
            "logged_in": False
        })

    return jsonify({
        "logged_in": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "credits": int(user["credits"])
        }
    })


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return jsonify({
        "success": True
    })


# =========================================================
# CREDITS
# =========================================================

@app.route("/credits")
@login_required
def credits():

    user_id = session["user_id"]

    balance = get_user_credits(user_id)

    return jsonify({
        "credits": balance,
        "costs": {
            "image": IMAGE_COST,
            "audio": AUDIO_COST,
            "video": VIDEO_COST,
            "generate_all": GENERATE_ALL_COST
        }
    })
# =========================================================
# IMAGE GENERATION
# =========================================================

@app.route("/generate", methods=["POST"])
@login_required
def generate():

    user_id = session["user_id"]

    try:

        # Check API key
        key_ok, key_error = check_gemini_key()

        if not key_ok:
            return jsonify({
                "error": key_error
            }), 500

        data = request.get_json(silent=True) or {}

        prompt = str(
            data.get("prompt", "")
        ).strip()

        if not prompt:

            return jsonify({
                "error": "Please enter a prompt."
            }), 400

        # Deduct image credits
        success, balance = use_credits(
            user_id,
            IMAGE_COST
        )

        if not success:

            return jsonify({
                "error": "Your credits are finished. Please recharge to continue.",
                "credits": balance
            }), 402

        # -------------------------------------------------
        # IMPORTANT:
        # Google Gemini 3.1 Flash Image
        #
        # We intentionally DO NOT send:
        # "mime_type": "image/png"
        #
        # because the current request that was failing
        # rejected that value in this configuration.
        # -------------------------------------------------

        payload = {
            "model": IMAGE_MODEL,

            "input": prompt,

            "response_format": {
                "type": "image",
                "aspect_ratio": "16:9",
                "image_size": "1K"
            }
        }

        response = requests.post(
            f"{BASE_URL}/interactions",
            headers=gemini_headers(),
            json=payload,
            timeout=180
        )

        print(
            "IMAGE STATUS:",
            response.status_code
        )

        print(
            "IMAGE RESPONSE:",
            response.text[:3000]
        )

        if response.status_code != 200:

            # Refund credits if Google rejected request
            refund_credits(
                user_id,
                IMAGE_COST
            )

            try:
                error_data = response.json()
            except Exception:
                error_data = response.text

            return jsonify({
                "error": "Image generation failed.",
                "details": error_data
            }), response.status_code

        result = response.json()

        # -------------------------------------------------
        # Current Interactions API output
        # -------------------------------------------------

        image_data = None

        if isinstance(result, dict):

            output_image = result.get(
                "output_image"
            )

            if isinstance(output_image, dict):

                image_data = output_image.get(
                    "data"
                )

        # -------------------------------------------------
        # Fallback: inspect steps
        # -------------------------------------------------

        if not image_data:

            steps = result.get(
                "steps",
                []
            )

            if isinstance(steps, list):

                for step in steps:

                    if not isinstance(step, dict):
                        continue

                    content = step.get(
                        "content",
                        []
                    )

                    if not isinstance(content, list):
                        continue

                    for block in content:

                        if not isinstance(block, dict):
                            continue

                        if block.get("type") == "image":

                            image_data = block.get(
                                "data"
                            )

                            if image_data:
                                break

                    if image_data:
                        break

        if not image_data:

            # Refund because no image was returned
            refund_credits(
                user_id,
                IMAGE_COST
            )

            return jsonify({
                "error": "Google returned no image data.",
                "response": result
            }), 500

        # -------------------------------------------------
        # Decode base64 image
        # -------------------------------------------------

        try:

            image_bytes = base64.b64decode(
                image_data
            )

        except Exception as e:

            refund_credits(
                user_id,
                IMAGE_COST
            )

            return jsonify({
                "error": "Could not decode generated image.",
                "details": str(e)
            }), 500

        # Save image
        with open(
            "generated.png",
            "wb"
        ) as image_file:

            image_file.write(
                image_bytes
            )

        return jsonify({
            "success": True,
            "image_url": "/generated.png",
            "credits": get_user_credits(user_id)
        })

    except requests.exceptions.Timeout:

        refund_credits(
            user_id,
            IMAGE_COST
        )

        return jsonify({
            "error": "Image generation timed out."
        }), 504

    except Exception as e:

        print(
            "IMAGE ERROR:",
            str(e)
        )

        refund_credits(
            user_id,
            IMAGE_COST
        )

        return jsonify({
            "error": "Image generation failed.",
            "details": str(e)
        }), 500


# =========================================================
# IMAGE FILE
# =========================================================

@app.route("/generated.png")
def generated_image():

    if not os.path.exists(
        "generated.png"
    ):

        return jsonify({
            "error": "No generated image found."
        }), 404

    return send_file(
        "generated.png",
        mimetype="image/png"
    )


# =========================================================
# REFUND CREDITS
# =========================================================

def refund_credits(user_id, amount):

    conn = get_db()

    try:

        cur = conn.cursor()

        if is_postgres():

            cur.execute(
                """
                UPDATE users
                SET credits = credits + %s
                WHERE id = %s
                """,
                (
                    amount,
                    user_id
                )
            )

        else:

            cur.execute(
                """
                UPDATE users
                SET credits = credits + ?
                WHERE id = ?
                """,
                (
                    amount,
                    user_id
                )
            )

        conn.commit()

    except Exception as e:

        conn.rollback()

        print(
            "REFUND ERROR:",
            str(e)
        )

    finally:

        conn.close()


# =========================================================
# AUDIO GENERATION
# =========================================================

@app.route("/audio", methods=["POST"])
@login_required
def audio():

    user_id = session["user_id"]

    try:

        key_ok, key_error = check_gemini_key()

        if not key_ok:

            return jsonify({
                "error": key_error
            }), 500

        data = request.get_json(
            silent=True
        ) or {}

        text = str(
            data.get("text", "")
        ).strip()

        # Frontend may send prompt instead of text
        if not text:
            text = str(
                data.get("prompt", "")
            ).strip()

        if not text:

            return jsonify({
                "error": "Please enter text."
            }), 400

        success, balance = use_credits(
            user_id,
            AUDIO_COST
        )

        if not success:

            return jsonify({
                "error": "Your credits are finished. Please recharge to continue.",
                "credits": balance
            }), 402

        payload = {

            "model": AUDIO_MODEL,

            "input": (
                "Speak the following text naturally and clearly. "
                "Do not add extra words.\n\n"
                + text
            ),

            "response_format": {
                "type": "audio"
            },

            "generation_config": {

                "speech_config": [
                    {
                        "voice": "Kore"
                    }
                ]
            }
        }

        response = requests.post(
            f"{BASE_URL}/interactions",
            headers=gemini_headers(),
            json=payload,
            timeout=180
        )

        print(
            "AUDIO STATUS:",
            response.status_code
        )

        print(
            "AUDIO RESPONSE:",
            response.text[:2000]
        )

        if response.status_code != 200:

            refund_credits(
                user_id,
                AUDIO_COST
            )

            try:
                error_data = response.json()
            except Exception:
                error_data = response.text

            return jsonify({
                "error": "Audio generation failed.",
                "details": error_data
            }), response.status_code

        result = response.json()

        audio_data = None

        output_audio = result.get(
            "output_audio"
        )

        if isinstance(
            output_audio,
            dict
        ):

            audio_data = output_audio.get(
                "data"
            )

        if not audio_data:

            refund_credits(
                user_id,
                AUDIO_COST
            )

            return jsonify({
                "error": "Google returned no audio data.",
                "response": result
            }), 500

        try:

            pcm_bytes = base64.b64decode(
                audio_data
            )

        except Exception as e:

            refund_credits(
                user_id,
                AUDIO_COST
            )

            return jsonify({
                "error": "Could not decode generated audio.",
                "details": str(e)
            }), 500

        # Gemini TTS returns PCM.
        # Convert PCM to WAV.
        save_pcm_as_wav(
            pcm_bytes,
            "generated.wav"
        )

        return jsonify({
            "success": True,
            "audio_url": "/generated.wav",
            "credits": get_user_credits(user_id)
        })

    except requests.exceptions.Timeout:

        refund_credits(
            user_id,
            AUDIO_COST
        )

        return jsonify({
            "error": "Audio generation timed out."
        }), 504

    except Exception as e:

        print(
            "AUDIO ERROR:",
            str(e)
        )

        refund_credits(
            user_id,
            AUDIO_COST
        )

        return jsonify({
            "error": "Audio generation failed.",
            "details": str(e)
        }), 500


# =========================================================
# PCM -> WAV
# =========================================================

def save_pcm_as_wav(
    pcm_data,
    filename,
    sample_rate=24000,
    channels=1,
    sample_width=2
):

    with wave.open(
        filename,
        "wb"
    ) as wav_file:

        wav_file.setnchannels(
            channels
        )

        wav_file.setsampwidth(
            sample_width
        )

        wav_file.setframerate(
            sample_rate
        )

        wav_file.writeframes(
            pcm_data
        )


# =========================================================
# AUDIO FILE
# =========================================================

@app.route("/generated.wav")
def generated_audio():

    if not os.path.exists(
        "generated.wav"
    ):

        return jsonify({
            "error": "No generated audio found."
        }), 404

    return send_file(
        "generated.wav",
        mimetype="audio/wav"
    )
    # =========================================================
# VIDEO GENERATION
# =========================================================

@app.route("/video", methods=["POST"])
@login_required
def video():

    user_id = session["user_id"]

    try:

        key_ok, key_error = check_gemini_key()

        if not key_ok:

            return jsonify({
                "error": key_error
            }), 500

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

        success, balance = use_credits(
            user_id,
            VIDEO_COST
        )

        if not success:

            return jsonify({
                "error": "Your credits are finished. Please recharge to continue.",
                "credits": balance
            }), 402

        # Create local job ID
        job_id = str(
            int(time.time() * 1000)
        ) + "_" + str(user_id)

        video_jobs[job_id] = {
            "status": "starting",
            "progress": 0,
            "video_url": None,
            "error": None
        }

        # Start generation in background
        thread = threading.Thread(
            target=generate_video_job,
            args=(
                job_id,
                user_id,
                prompt
            ),
            daemon=True
        )

        thread.start()

        return jsonify({
            "success": True,
            "job_id": job_id,
            "credits": get_user_credits(user_id)
        })

    except Exception as e:

        print(
            "VIDEO ERROR:",
            str(e)
        )

        return jsonify({
            "error": "Video generation failed.",
            "details": str(e)
        }), 500


# =========================================================
# VIDEO BACKGROUND JOB
# =========================================================

def generate_video_job(
    job_id,
    user_id,
    prompt
):

    try:

        video_jobs[job_id] = {
            "status": "generating",
            "progress": 5,
            "video_url": None,
            "error": None
        }

        # -------------------------------------------------
        # Current Veo 3.1 Fast API
        # -------------------------------------------------

        payload = {
            "instances": [
                {
                    "prompt": prompt
                }
            ],

            "parameters": {
                "aspectRatio": "16:9",
                "resolution": "720p",
                "durationSeconds": "8",
                "numberOfVideos": 1
            }
        }

        response = requests.post(
            f"{BASE_URL}/models/{VIDEO_MODEL}:predictLongRunning",
            headers=gemini_headers(),
            json=payload,
            timeout=120
        )

        print(
            "VIDEO START STATUS:",
            response.status_code
        )

        print(
            "VIDEO START RESPONSE:",
            response.text[:3000]
        )

        if response.status_code != 200:

            refund_credits(
                user_id,
                VIDEO_COST
            )

            try:
                error_data = response.json()
            except Exception:
                error_data = response.text

            video_jobs[job_id] = {
                "status": "failed",
                "progress": 0,
                "video_url": None,
                "error": str(error_data)
            }

            return

        operation = response.json()

        operation_name = operation.get(
            "name"
        )

        if not operation_name:

            refund_credits(
                user_id,
                VIDEO_COST
            )

            video_jobs[job_id] = {
                "status": "failed",
                "progress": 0,
                "video_url": None,
                "error": "Google did not return an operation name."
            }

            return

        # -------------------------------------------------
        # Poll operation
        # -------------------------------------------------

        for attempt in range(120):

            time.sleep(10)

            status_response = requests.get(
                f"{BASE_URL}/{operation_name}",
                headers={
                    "x-goog-api-key": GEMINI_API_KEY
                },
                timeout=60
            )

            print(
                "VIDEO POLL:",
                status_response.status_code
            )

            if status_response.status_code != 200:

                refund_credits(
                    user_id,
                    VIDEO_COST
                )

                video_jobs[job_id] = {
                    "status": "failed",
                    "progress": 0,
                    "video_url": None,
                    "error": status_response.text
                }

                return

            status_data = status_response.json()

            if status_data.get("done") is True:

                # Check Google error
                if "error" in status_data:

                    refund_credits(
                        user_id,
                        VIDEO_COST
                    )

                    video_jobs[job_id] = {
                        "status": "failed",
                        "progress": 0,
                        "video_url": None,
                        "error": str(
                            status_data["error"]
                        )
                    }

                    return

                video_uri = extract_video_uri(
                    status_data
                )

                if not video_uri:

                    refund_credits(
                        user_id,
                        VIDEO_COST
                    )

                    video_jobs[job_id] = {
                        "status": "failed",
                        "progress": 0,
                        "video_url": None,
                        "error": (
                            "Video completed but "
                            "Google returned no video URL."
                        )
                    }

                    return

                # Download video
                download_response = requests.get(
                    video_uri,
                    headers={
                        "x-goog-api-key": GEMINI_API_KEY
                    },
                    timeout=180,
                    allow_redirects=True
                )

                if download_response.status_code != 200:

                    refund_credits(
                        user_id,
                        VIDEO_COST
                    )

                    video_jobs[job_id] = {
                        "status": "failed",
                        "progress": 0,
                        "video_url": None,
                        "error": (
                            "Could not download generated video."
                        )
                    }

                    return

                with open(
                    "generated.mp4",
                    "wb"
                ) as video_file:

                    video_file.write(
                        download_response.content
                    )

                video_jobs[job_id] = {
                    "status": "completed",
                    "progress": 100,
                    "video_url": "/generated.mp4",
                    "error": None
                }

                return

            # Still running
            progress = min(
                95,
                10 + (attempt * 1)
            )

            video_jobs[job_id] = {
                "status": "generating",
                "progress": progress,
                "video_url": None,
                "error": None
            }

        # Timeout
        refund_credits(
            user_id,
            VIDEO_COST
        )

        video_jobs[job_id] = {
            "status": "failed",
            "progress": 0,
            "video_url": None,
            "error": "Video generation timed out."
        }

    except Exception as e:

        print(
            "VIDEO JOB ERROR:",
            str(e)
        )

        refund_credits(
            user_id,
            VIDEO_COST
        )

        video_jobs[job_id] = {
            "status": "failed",
            "progress": 0,
            "video_url": None,
            "error": str(e)
        }


# =========================================================
# EXTRACT VIDEO URI
# =========================================================

def extract_video_uri(data):

    try:

        response = data.get(
            "response",
            {}
        )

        generate_response = response.get(
            "generateVideoResponse",
            {}
        )

        generated_samples = generate_response.get(
            "generatedSamples",
            []
        )

        if generated_samples:

            first_sample = generated_samples[0]

            video = first_sample.get(
                "video",
                {}
            )

            uri = video.get(
                "uri"
            )

            if uri:
                return uri

    except Exception as e:

        print(
            "VIDEO URI ERROR:",
            str(e)
        )

    return None


# =========================================================
# VIDEO STATUS
# =========================================================

@app.route(
    "/video/status/<job_id>",
    methods=["GET"]
)
@login_required
def video_status(job_id):

    job = video_jobs.get(
        job_id
    )

    if not job:

        return jsonify({
            "error": "Video job not found."
        }), 404

    return jsonify({
        "status": job.get("status"),
        "progress": job.get("progress", 0),
        "video_url": job.get("video_url"),
        "error": job.get("error")
    })


# =========================================================
# VIDEO FILE
# =========================================================

@app.route("/generated.mp4")
def generated_video():

    if not os.path.exists(
        "generated.mp4"
    ):

        return jsonify({
            "error": "No generated video found."
        }), 404

    return send_file(
        "generated.mp4",
        mimetype="video/mp4"
    )


# =========================================================
# RECHARGE
# =========================================================

@app.route(
    "/recharge",
    methods=["POST"]
)
@login_required
def recharge():

    return jsonify({
        "error": (
            "Recharge is currently unavailable. "
            "Payment system is not connected yet."
        )
    }), 403


# =========================================================
# START APP
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