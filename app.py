from flask import Flask, request, jsonify, send_file, session
from huggingface_hub import InferenceClient, get_token
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

import sqlite3
import os
import shutil
import uuid
import threading
import time


# =========================================================
# APP CONFIG
# =========================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "my-ai-studio-secret-key-2026"
)

DATABASE = "users.db"

HF_TOKEN = os.getenv("HF_TOKEN") or get_token()


# =========================================================
# CREDITS
# =========================================================

NEW_USER_CREDITS = 100

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

    connection = sqlite3.connect(
        DATABASE,
        timeout=30
    )

    connection.execute(
        "PRAGMA busy_timeout=30000"
    )

    connection.execute(
        "PRAGMA journal_mode=WAL"
    )

    return connection


def init_database():

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            credits INTEGER NOT NULL DEFAULT 100
        )
    """)

    connection.commit()

    connection.close()


init_database()


# =========================================================
# LOGIN REQUIRED
# =========================================================

def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:

            return jsonify({
                "error": "Please login first"
            }), 401

        return function(*args, **kwargs)

    return wrapper


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return send_file("index.html")


# =========================================================
# REGISTER
# =========================================================

@app.route("/register", methods=["POST"])
def register():

    try:

        data = request.get_json() or {}

        username = data.get(
            "username",
            ""
        ).strip()

        password = data.get(
            "password",
            ""
        )

        if not username or not password:

            return jsonify({
                "error": "Username and password are required"
            }), 400

        if len(username) < 3:

            return jsonify({
                "error": "Username must be at least 3 characters"
            }), 400

        if len(password) < 4:

            return jsonify({
                "error": "Password must be at least 4 characters"
            }), 400

        password_hash = generate_password_hash(
            password
        )

        connection = get_db()

        cursor = connection.cursor()

        try:

            cursor.execute(
                """
                INSERT INTO users
                (username, password, credits)
                VALUES (?, ?, ?)
                """,
                (
                    username,
                    password_hash,
                    NEW_USER_CREDITS
                )
            )

            connection.commit()

        except sqlite3.IntegrityError:

            connection.close()

            return jsonify({
                "error": "Username already exists"
            }), 409

        connection.close()

        return jsonify({
            "message": "Registration successful",
            "credits": NEW_USER_CREDITS
        })

    except Exception as e:

        print(
            "REGISTER ERROR:",
            str(e)
        )

        return jsonify({
            "error": "Registration failed"
        }), 500


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["POST"])
def login():

    try:

        data = request.get_json() or {}

        username = data.get(
            "username",
            ""
        ).strip()

        password = data.get(
            "password",
            ""
        )

        connection = get_db()

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id, username, password, credits
            FROM users
            WHERE username = ?
            """,
            (username,)
        )

        user = cursor.fetchone()

        connection.close()

        if not user:

            return jsonify({
                "error": "Invalid username or password"
            }), 401

        user_id = user[0]
        stored_username = user[1]
        stored_password = user[2]

        if not check_password_hash(
            stored_password,
            password
        ):

            return jsonify({
                "error": "Invalid username or password"
            }), 401

        session["user_id"] = user_id
        session["username"] = stored_username

        return jsonify({
            "message": "Login successful",
            "username": stored_username,
            "credits": user[3]
        })

    except Exception as e:

        print(
            "LOGIN ERROR:",
            str(e)
        )

        return jsonify({
            "error": "Login failed"
        }), 500


# =========================================================
# CURRENT USER
# =========================================================

@app.route("/me")
def me():

    if "user_id" in session:

        return jsonify({
            "logged_in": True,
            "username": session.get(
                "username"
            )
        })

    return jsonify({
        "logged_in": False
    })


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout", methods=["POST"])
def logout():

    session.clear()

    return jsonify({
        "message": "Logged out successfully"
    })


# =========================================================
# CREDITS
# =========================================================

@app.route("/credits")
@login_required
def credits():

    try:

        connection = get_db()

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT credits
            FROM users
            WHERE id = ?
            """,
            (
                session["user_id"],
            )
        )

        result = cursor.fetchone()

        connection.close()

        if not result:

            return jsonify({
                "error": "User not found"
            }), 404

        return jsonify({
            "credits": result[0]
        })

    except Exception as e:

        print(
            "CREDITS ERROR:",
            str(e)
        )

        return jsonify({
            "error": "Could not load credits"
        }), 500


# =========================================================
# CREDIT HELPERS
# =========================================================

def get_user_credits(user_id):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT credits
        FROM users
        WHERE id = ?
        """,
        (user_id,)
    )

    result = cursor.fetchone()

    connection.close()

    if not result:

        return None

    return result[0]


def change_credits(
    user_id,
    amount
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
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

    connection.commit()

    connection.close()


def reserve_credits(
    user_id,
    cost
):

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT credits
        FROM users
        WHERE id = ?
        """,
        (user_id,)
    )

    result = cursor.fetchone()

    if not result:

        connection.close()

        return False

    current_credits = result[0]

    if current_credits < cost:

        connection.close()

        return False

    cursor.execute(
        """
        UPDATE users
        SET credits = credits - ?
        WHERE id = ?
        AND credits >= ?
        """,
        (
            cost,
            user_id,
            cost
        )
    )

    updated = cursor.rowcount

    connection.commit()

    connection.close()

    return updated == 1
# =========================================================
# IMAGE GENERATION
# HUGGING FACE INFERENCE PROVIDERS
# =========================================================

@app.route("/generate", methods=["POST"])
@login_required
def generate():

    user_id = session["user_id"]

    try:

        data = request.get_json() or {}

        prompt = data.get(
            "prompt",
            ""
        ).strip()

        if not prompt:

            return jsonify({
                "error": "Please enter a prompt"
            }), 400

        # Reserve 5 credits
        if not reserve_credits(
            user_id,
            IMAGE_COST
        ):

            return jsonify({
                "error": "Your credits are finished. Please recharge to continue."
            }), 402

        try:

            # Create Hugging Face Inference Provider client.
            # This does NOT use the old ZeroGPU Space.
            image_client = InferenceClient(
                api_key=HF_TOKEN,
                provider="auto"
            )

            image = image_client.text_to_image(

                prompt,

                model="black-forest-labs/FLUX.1-schnell",

                width=1024,

                height=1024,

                num_inference_steps=4

            )

            # Save PIL image
            image.save(
                "generated.png"
            )

            return jsonify({
                "image_url": "/generated.png"
            })

        except Exception as e:

            # Give credits back if generation fails
            change_credits(
                user_id,
                IMAGE_COST
            )

            print(
                "IMAGE ERROR:",
                str(e)
            )

            return jsonify({
                "error": "Image generation failed: " + str(e)
            }), 500

    except Exception as e:

        print(
            "GENERATE ERROR:",
            str(e)
        )

        return jsonify({
            "error": "Something went wrong"
        }), 500


# =========================================================
# GENERATED IMAGE FILE
# =========================================================

@app.route("/generated.png")
def generated_image():

    if not os.path.exists(
        "generated.png"
    ):

        return jsonify({
            "error": "Image not found"
        }), 404

    return send_file(
        "generated.png",
        mimetype="image/png"
    )


# =========================================================
# AUDIO GENERATION
# =========================================================

@app.route("/audio", methods=["POST"])
@login_required
def audio():

    user_id = session["user_id"]

    try:

        data = request.get_json() or {}

        text = data.get(
            "text",
            ""
        ).strip()

        if not text:

            return jsonify({
                "error": "Please enter text"
            }), 400

        if not reserve_credits(
            user_id,
            AUDIO_COST
        ):

            return jsonify({
                "error": "Your credits are finished. Please recharge to continue."
            }), 402

        try:

            from gradio_client import Client

            audio_client = Client(
                "Remsky/Kokoro-TTS-Zero",
                token=HF_TOKEN
            )

            result = audio_client.predict(

                text,

                ["af_heart"],

                1,

                api_name="/generate_speech_from_ui"

            )

            audio_path = result[0]

            shutil.copy(
                audio_path,
                "generated.wav"
            )

            return jsonify({
                "audio_url": "/generated.wav"
            })

        except Exception as e:

            change_credits(
                user_id,
                AUDIO_COST
            )

            print(
                "AUDIO ERROR:",
                str(e)
            )

            return jsonify({
                "error": "Audio generation failed: " + str(e)
            }), 500

    except Exception as e:

        print(
            "AUDIO ROUTE ERROR:",
            str(e)
        )

        return jsonify({
            "error": "Something went wrong"
        }), 500


# =========================================================
# GENERATED AUDIO FILE
# =========================================================

@app.route("/generated.wav")
def generated_audio():

    if not os.path.exists(
        "generated.wav"
    ):

        return jsonify({
            "error": "Audio not found"
        }), 404

    return send_file(
        "generated.wav",
        mimetype="audio/wav"
    )


# =========================================================
# VIDEO PATH EXTRACTOR
# =========================================================

def extract_video_path(value):

    if isinstance(
        value,
        str
    ):

        return value

    if isinstance(
        value,
        dict
    ):

        if value.get("video"):

            return extract_video_path(
                value["video"]
            )

        if value.get("path"):

            return extract_video_path(
                value["path"]
            )

        if value.get("filepath"):

            return extract_video_path(
                value["filepath"]
            )

        if value.get("url"):

            return value["url"]

        return None

    if hasattr(
        value,
        "__fspath__"
    ):

        return os.fspath(value)

    if hasattr(
        value,
        "path"
    ):

        return value.path

    if hasattr(
        value,
        "url"
    ):

        return value.url

    return None


# =========================================================
# VIDEO BACKGROUND WORKER
# =========================================================

def generate_video_background(
    job_id,
    user_id,
    prompt
):

    try:

        video_jobs[job_id] = {
            "status": "generating",
            "message": "Video generation started",
            "created_at": time.time()
        }

        from gradio_client import Client

        video_client = Client(
            "Lightricks/ltx-video-distilled",
            token=HF_TOKEN
        )

        result = video_client.predict(

            prompt,

            "worst quality, low quality, blurry, distorted, jittery, flickering, noisy, deformed, inconsistent motion",

            None,

            None,

            512,

            768,

            "text-to-video",

            5.0,

            9,

            42,

            True,

            3.0,

            True,

            api_name="/text_to_video"

        )

        video_value = result[0]

        video_path = extract_video_path(
            video_value
        )

        if not video_path:

            raise Exception(
                "Could not find generated video file"
            )

        if os.path.exists(video_path):

            shutil.copy(
                video_path,
                "generated.mp4"
            )

        else:

            raise Exception(
                "Generated video file does not exist"
            )

        video_jobs[job_id] = {
            "status": "completed",
            "video_url": "/generated.mp4",
            "created_at": time.time()
        }

    except Exception as e:

        print(
            "VIDEO ERROR:",
            str(e)
        )

        change_credits(
            user_id,
            VIDEO_COST
        )

        video_jobs[job_id] = {
            "status": "failed",
            "message": str(e),
            "created_at": time.time()
        }


# =========================================================
# START VIDEO GENERATION
# =========================================================

@app.route("/video", methods=["POST"])
@login_required
def video():

    user_id = session["user_id"]

    try:

        data = request.get_json() or {}

        prompt = data.get(
            "prompt",
            ""
        ).strip()

        if not prompt:

            return jsonify({
                "error": "Please enter a prompt"
            }), 400

        if not reserve_credits(
            user_id,
            VIDEO_COST
        ):

            return jsonify({
                "error": "Your credits are finished. Please recharge to continue."
            }), 402

        job_id = str(
            uuid.uuid4()
        )

        video_jobs[job_id] = {
            "status": "queued",
            "message": "Video request queued",
            "created_at": time.time()
        }

        worker = threading.Thread(

            target=generate_video_background,

            args=(
                job_id,
                user_id,
                prompt
            ),

            daemon=True

        )

        worker.start()

        return jsonify({

            "message": "Video generation started",

            "job_id": job_id,

            "status": "queued"

        }), 202

    except Exception as e:

        print(
            "VIDEO ROUTE ERROR:",
            str(e)
        )

        return jsonify({
            "error": "Could not start video generation"
        }), 500


# =========================================================
# VIDEO STATUS
# =========================================================

@app.route(
    "/video/status/<job_id>"
)
@login_required
def video_status(job_id):

    job = video_jobs.get(
        job_id
    )

    if not job:

        return jsonify({
            "error": "Video job not found"
        }), 404

    return jsonify(job)
    # =========================================================
# GENERATED VIDEO FILE
# =========================================================

@app.route("/generated.mp4")
def generated_video():

    if not os.path.exists(
        "generated.mp4"
    ):

        return jsonify({
            "error": "Video not found"
        }), 404

    return send_file(
        "generated.mp4",
        mimetype="video/mp4"
    )


# =========================================================
# RECHARGE
# =========================================================

@app.route("/recharge", methods=["POST"])
@login_required
def recharge():

    return jsonify({
        "error": "Recharge is currently unavailable. Payment system is not connected yet."
    }), 403


# =========================================================
# HEALTH CHECK
# =========================================================

@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "service": "My AI Studio"
    })


# =========================================================
# ERROR HANDLERS
# =========================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({
        "error": "Page or resource not found"
    }), 404


@app.errorhandler(500)
def internal_error(error):

    return jsonify({
        "error": "Internal server error"
    }), 500


# =========================================================
# CLEANUP OLD VIDEO JOBS
# =========================================================

def cleanup_video_jobs():

    while True:

        try:

            current_time = time.time()

            expired_jobs = []

            for job_id, job in list(
                video_jobs.items()
            ):

                created_at = job.get(
                    "created_at"
                )

                if created_at:

                    if (
                        current_time - created_at
                        > 3600
                    ):

                        expired_jobs.append(
                            job_id
                        )

            for job_id in expired_jobs:

                video_jobs.pop(
                    job_id,
                    None
                )

        except Exception as e:

            print(
                "CLEANUP ERROR:",
                str(e)
            )

        time.sleep(600)


# =========================================================
# START CLEANUP THREAD
# =========================================================

cleanup_thread = threading.Thread(

    target=cleanup_video_jobs,

    daemon=True

)

cleanup_thread.start()


# =========================================================
# RUN APP
# =========================================================

if __name__ == "__main__":

    print("=" * 50)

    print(
        "My AI Studio starting..."
    )

    print(
        "Local URL: http://127.0.0.1:5000"
    )

    print(
        "Image cost:",
        IMAGE_COST
    )

    print(
        "Audio cost:",
        AUDIO_COST
    )

    print(
        "Video cost:",
        VIDEO_COST
    )

    print(
        "New user credits:",
        NEW_USER_CREDITS
    )

    print("=" * 50)

    app.run(

        host="0.0.0.0",

        port=int(
            os.getenv(
                "PORT",
                5000
            )
        ),

        debug=False

    )