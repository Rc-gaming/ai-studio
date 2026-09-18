from flask import Flask, request, jsonify, send_file, session
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

import sqlite3
import os
import base64
import wave
import uuid
import threading
import time
import requests

try:
    import psycopg2
except ImportError:
    psycopg2 = None


app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "my-ai-studio-secret-key-2026"
)


# =========================
# GOOGLE GEMINI
# =========================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

IMAGE_MODEL = "gemini-3.1-flash-image"

AUDIO_MODEL = "gemini-3.1-flash-tts-preview"

VIDEO_MODEL = "veo-3.1-fast-generate-preview"


# =========================
# CREDITS
# =========================

IMAGE_COST = 5
AUDIO_COST = 8
VIDEO_COST = 21

NEW_USER_CREDITS = 100


# =========================
# DATABASE
# =========================

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

SQLITE_DATABASE = "users.db"


def using_postgres():
    return bool(DATABASE_URL)


def get_connection():

    if using_postgres():

        if psycopg2 is None:
            raise RuntimeError(
                "psycopg2-binary is not installed"
            )

        return psycopg2.connect(DATABASE_URL)

    connection = sqlite3.connect(
        SQLITE_DATABASE,
        timeout=30
    )

    connection.row_factory = sqlite3.Row

    return connection


def sql_query(query):

    if using_postgres():
        return query.replace("?", "%s")

    return query


def init_database():

    connection = get_connection()

    cursor = connection.cursor()

    if using_postgres():

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                credits INTEGER NOT NULL DEFAULT 100,
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    connection.commit()

    cursor.close()
    connection.close()


init_database()


def db_one(query, params=()):

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        sql_query(query),
        params
    )

    row = cursor.fetchone()

    cursor.close()
    connection.close()

    return row


def db_all(query, params=()):

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        sql_query(query),
        params
    )

    rows = cursor.fetchall()

    cursor.close()
    connection.close()

    return rows


def reserve_credits(user_id, amount):

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        sql_query(
            """
            UPDATE users
            SET credits = credits - ?
            WHERE id = ?
            AND credits >= ?
            """
        ),
        (
            amount,
            user_id,
            amount
        )
    )

    success = cursor.rowcount == 1

    connection.commit()

    cursor.close()
    connection.close()

    return success


def change_credits(user_id, amount):

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        sql_query(
            """
            UPDATE users
            SET credits = credits + ?
            WHERE id = ?
            """
        ),
        (
            amount,
            user_id
        )
    )

    connection.commit()

    cursor.close()
    connection.close()


def get_credits(user_id):

    row = db_one(
        "SELECT credits FROM users WHERE id = ?",
        (user_id,)
    )

    if not row:
        return 0

    return int(row[0])


# =========================
# LOGIN REQUIRED
# =========================

def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:

            return jsonify({
                "error": "Please login first"
            }), 401

        return function(*args, **kwargs)

    return wrapper


# =========================
# GOOGLE HELPERS
# =========================

def require_gemini_key():

    if not GEMINI_API_KEY:

        raise RuntimeError(
            "GEMINI_API_KEY is missing. "
            "Add it in Render Environment."
        )


def gemini_headers():

    return {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json"
    }


def gemini_error(response):

    try:

        data = response.json()

        error = data.get("error", {})

        message = (
            error.get("message")
            or data.get("message")
            or str(data)
        )

        return str(message)

    except Exception:

        return (
            response.text[:1000]
            or f"HTTP {response.status_code}"
        )


def extract_output_data(data, wanted_type):

    if not isinstance(data, dict):
        return None


    # Direct output_image / output_audio

    if wanted_type == "image":

        output = data.get("output_image")

    else:

        output = data.get("output_audio")


    if isinstance(output, dict):

        if output.get("data"):
            return output["data"]


    # Steps

    steps = data.get("steps", [])

    for step in steps:

        if not isinstance(step, dict):
            continue

        content = step.get("content", [])

        if isinstance(content, dict):
            content = [content]

        for block in content:

            if not isinstance(block, dict):
                continue

            if block.get("type") == wanted_type:

                if block.get("data"):
                    return block["data"]


    return None


# =========================
# HOME / HEALTH
# =========================

@app.route("/")
def home():

    return send_file("index.html")


@app.route("/health")
def health():

    return jsonify({
        "ok": True,
        "database": (
            "postgres"
            if using_postgres()
            else "sqlite"
        ),
        "gemini_configured": bool(
            GEMINI_API_KEY
        )
    })


# =========================
# REGISTER
# =========================

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


        if len(username) < 3:

            return jsonify({
                "error":
                "Username must be at least 3 characters"
            }), 400


        if len(password) < 4:

            return jsonify({
                "error":
                "Password must be at least 4 characters"
            }), 400


        existing = db_one(
            "SELECT id FROM users WHERE username = ?",
            (username,)
        )


        if existing:

            return jsonify({
                "error":
                "Username already exists"
            }), 409


        password_hash = generate_password_hash(
            password
        )


        connection = get_connection()

        cursor = connection.cursor()


        if using_postgres():

            cursor.execute(
                sql_query(
                    """
                    INSERT INTO users
                    (username, password, credits)
                    VALUES (?, ?, ?)
                    RETURNING id
                    """
                ),
                (
                    username,
                    password_hash,
                    NEW_USER_CREDITS
                )
            )

            user_id = cursor.fetchone()[0]


        else:

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

            user_id = cursor.lastrowid


        connection.commit()

        cursor.close()
        connection.close()


        session["user_id"] = user_id

        session["username"] = username


        return jsonify({
            "message":
            "Registration successful",

            "username":
            username,

            "credits":
            NEW_USER_CREDITS
        })


    except Exception as e:

        print(
            "REGISTER ERROR:",
            str(e)
        )

        return jsonify({
            "error":
            "Registration failed: "
            + str(e)
        }), 500


# =========================
# LOGIN
# =========================

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


        row = db_one(
            """
            SELECT id, password
            FROM users
            WHERE username = ?
            """,
            (username,)
        )


        if (
            not row
            or not check_password_hash(
                row[1],
                password
            )
        ):

            return jsonify({
                "error":
                "Invalid username or password"
            }), 401


        session["user_id"] = row[0]

        session["username"] = username


        return jsonify({
            "message":
            "Login successful",

            "username":
            username,

            "credits":
            get_credits(row[0])
        })


    except Exception as e:

        print(
            "LOGIN ERROR:",
            str(e)
        )

        return jsonify({
            "error":
            "Login failed: "
            + str(e)
        }), 500


# =========================
# ME
# =========================

@app.route("/me")
def me():

    if "user_id" in session:

        return jsonify({

            "logged_in":
            True,

            "username":
            session.get("username"),

            "credits":
            get_credits(
                session["user_id"]
            )

        })


    return jsonify({
        "logged_in": False
    })


# =========================
# LOGOUT
# =========================

@app.route("/logout", methods=["POST"])
def logout():

    session.clear()

    return jsonify({
        "message":
        "Logged out successfully"
    })


# =========================
# CREDITS
# =========================

@app.route("/credits")
@login_required
def credits():

    return jsonify({
        "credits":
        get_credits(
            session["user_id"]
        )
    })
# =========================
# IMAGE GENERATION
# =========================

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
                "error":
                "Please enter a prompt"
            }), 400


        if not reserve_credits(
            user_id,
            IMAGE_COST
        ):

            return jsonify({
                "error":
                "Your credits are finished. "
                "Please recharge to continue."
            }), 402


        try:

            require_gemini_key()


            payload = {

                "model":
                IMAGE_MODEL,

                "input":
                prompt,

                "response_format": {

                    "type":
                    "image",

                    "mime_type":
                    "image/png",

                    "aspect_ratio":
                    "16:9",

                    "image_size":
                    "1K"
                }
            }


            response = requests.post(

                GEMINI_BASE
                + "/interactions",

                headers=
                gemini_headers(),

                json=
                payload,

                timeout=180
            )


            if not response.ok:

                raise RuntimeError(
                    gemini_error(response)
                )


            result = response.json()


            image_b64 = extract_output_data(
                result,
                "image"
            )


            if not image_b64:

                raise RuntimeError(
                    "Google returned no image data."
                )


            with open(
                "generated.png",
                "wb"
            ) as image_file:

                image_file.write(
                    base64.b64decode(
                        image_b64
                    )
                )


            return jsonify({

                "image_url":
                "/generated.png",

                "credits":
                get_credits(user_id)

            })


        except Exception as e:

            change_credits(
                user_id,
                IMAGE_COST
            )

            print(
                "IMAGE ERROR:",
                str(e)
            )

            return jsonify({

                "error":
                "Image generation failed: "
                + str(e)

            }), 500


    except Exception as e:

        print(
            "GENERATE ERROR:",
            str(e)
        )

        return jsonify({

            "error":
            "Something went wrong: "
            + str(e)

        }), 500


@app.route("/generated.png")
def generated_image():

    if not os.path.exists(
        "generated.png"
    ):

        return jsonify({
            "error":
            "No image generated yet"
        }), 404


    return send_file(
        "generated.png",
        mimetype="image/png"
    )


# =========================
# AUDIO / TTS
# =========================

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
                "error":
                "Please enter text"
            }), 400


        if not reserve_credits(
            user_id,
            AUDIO_COST
        ):

            return jsonify({
                "error":
                "Your credits are finished. "
                "Please recharge to continue."
            }), 402


        try:

            require_gemini_key()


            payload = {

                "model":
                AUDIO_MODEL,

                "input":
                text,

                "response_format": {

                    "type":
                    "audio"
                },

                "generation_config": {

                    "speech_config": [

                        {
                            "voice":
                            "Kore"
                        }

                    ]

                }

            }


            response = requests.post(

                GEMINI_BASE
                + "/interactions",

                headers=
                gemini_headers(),

                json=
                payload,

                timeout=180
            )


            if not response.ok:

                raise RuntimeError(
                    gemini_error(response)
                )


            result = response.json()


            audio_b64 = extract_output_data(
                result,
                "audio"
            )


            if not audio_b64:

                raise RuntimeError(
                    "Google returned no audio data."
                )


            pcm = base64.b64decode(
                audio_b64
            )


            with wave.open(
                "generated.wav",
                "wb"
            ) as wf:

                wf.setnchannels(1)

                wf.setsampwidth(2)

                wf.setframerate(24000)

                wf.writeframes(pcm)


            return jsonify({

                "audio_url":
                "/generated.wav",

                "credits":
                get_credits(user_id)

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

                "error":
                "Audio generation failed: "
                + str(e)

            }), 500


    except Exception as e:

        print(
            "AUDIO ROUTE ERROR:",
            str(e)
        )

        return jsonify({

            "error":
            "Something went wrong: "
            + str(e)

        }), 500


@app.route("/generated.wav")
def generated_audio():

    if not os.path.exists(
        "generated.wav"
    ):

        return jsonify({
            "error":
            "No audio generated yet"
        }), 404


    return send_file(
        "generated.wav",
        mimetype="audio/wav"
    )


# =========================
# VIDEO JOB STORAGE
# =========================

video_jobs = {}

video_jobs_lock = threading.Lock()


# =========================
# VIDEO WORKER
# =========================

def video_worker(
    job_id,
    user_id,
    prompt
):

    try:

        require_gemini_key()


        payload = {

            "instances": [

                {
                    "prompt":
                    prompt
                }

            ],

            "parameters": {

                "aspectRatio":
                "16:9",

                "resolution":
                "720p",

                "durationSeconds":
                "8",

                "numberOfVideos":
                1

            }

        }


        response = requests.post(

            GEMINI_BASE
            + "/models/"
            + VIDEO_MODEL
            + ":predictLongRunning",

            headers=
            gemini_headers(),

            json=
            payload,

            timeout=60
        )


        if not response.ok:

            raise RuntimeError(
                gemini_error(response)
            )


        operation = response.json()


        operation_name = operation.get(
            "name"
        )


        if not operation_name:

            raise RuntimeError(
                "Google did not return "
                "a video operation."
            )


        with video_jobs_lock:

            video_jobs[job_id][
                "status"
            ] = "processing"


        deadline = time.time() + 900


        while time.time() < deadline:

            time.sleep(10)


            status_response = requests.get(

                GEMINI_BASE
                + "/"
                + operation_name,

                headers={
                    "x-goog-api-key":
                    GEMINI_API_KEY
                },

                timeout=60
            )


            if not status_response.ok:

                raise RuntimeError(
                    gemini_error(
                        status_response
                    )
                )


            status = status_response.json()


            if not status.get("done"):

                continue


            if status.get("error"):

                raise RuntimeError(

                    status["error"].get(
                        "message",
                        "Video generation failed"
                    )

                )


            samples = (

                status

                .get("response", {})

                .get(
                    "generateVideoResponse",
                    {}
                )

                .get(
                    "generatedSamples",
                    []
                )

            )


            if not samples:

                raise RuntimeError(
                    "Google returned no generated video."
                )


            video_info = samples[0].get(
                "video",
                {}
            )


            video_uri = video_info.get(
                "uri"
            )


            if not video_uri:

                raise RuntimeError(
                    "Google returned no video URL."
                )


            video_response = requests.get(

                video_uri,

                headers={
                    "x-goog-api-key":
                    GEMINI_API_KEY
                },

                timeout=180
            )


            if not video_response.ok:

                raise RuntimeError(
                    "Could not download generated video: "
                    + gemini_error(
                        video_response
                    )
                )


            with open(
                "generated.mp4",
                "wb"
            ) as video_file:

                video_file.write(
                    video_response.content
                )


            with video_jobs_lock:

                video_jobs[job_id][
                    "status"
                ] = "completed"

                video_jobs[job_id][
                    "video_url"
                ] = "/generated.mp4"

                video_jobs[job_id][
                    "credits"
                ] = get_credits(
                    user_id
                )


            return


        raise RuntimeError(
            "Video generation timed out."
        )


    except Exception as e:

        print(
            "VIDEO ERROR:",
            str(e)
        )


        # Give credits back if generation failed

        change_credits(
            user_id,
            VIDEO_COST
        )


        with video_jobs_lock:

            video_jobs[job_id][
                "status"
            ] = "failed"

            video_jobs[job_id][
                "error"
            ] = str(e)

            video_jobs[job_id][
                "credits"
            ] = get_credits(
                user_id
            )


# =========================
# VIDEO START
# =========================

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
                "error":
                "Please enter a prompt"
            }), 400


        if not reserve_credits(
            user_id,
            VIDEO_COST
        ):

            return jsonify({
                "error":
                "Your credits are finished. "
                "Please recharge to continue."
            }), 402


        job_id = uuid.uuid4().hex


        with video_jobs_lock:

            video_jobs[job_id] = {

                "status":
                "starting",

                "video_url":
                None,

                "error":
                None,

                "credits":
                get_credits(user_id)

            }


        thread = threading.Thread(

            target=video_worker,

            args=(
                job_id,
                user_id,
                prompt
            ),

            daemon=True
        )


        thread.start()


        return jsonify({

            "job_id":
            job_id,

            "status":
            "starting",

            "credits":
            get_credits(user_id)

        })


    except Exception as e:

        print(
            "VIDEO ROUTE ERROR:",
            str(e)
        )

        return jsonify({

            "error":
            "Video request failed: "
            + str(e)

        }), 500
    # =========================
# VIDEO STATUS
# =========================

@app.route(
    "/video/status/<job_id>"
)
@login_required
def video_status(job_id):

    with video_jobs_lock:

        job = video_jobs.get(
            job_id
        )


    if not job:

        return jsonify({

            "error":
            "Video job not found. "
            "The server may have restarted."

        }), 404


    return jsonify(job)


# =========================
# VIDEO FILE
# =========================

@app.route("/generated.mp4")
def generated_video():

    if not os.path.exists(
        "generated.mp4"
    ):

        return jsonify({

            "error":
            "No video generated yet"

        }), 404


    return send_file(

        "generated.mp4",

        mimetype="video/mp4"
    )


# =========================
# RECHARGE
# =========================

@app.route(
    "/recharge",
    methods=["POST"]
)
@login_required
def recharge():

    return jsonify({

        "error":
        "Recharge payment system "
        "is not connected yet."

    }), 403


# =========================
# ERROR HANDLERS
# =========================

@app.errorhandler(404)
def not_found(error):

    return jsonify({

        "error":
        "Route not found"

    }), 404


@app.errorhandler(500)
def server_error(error):

    return jsonify({

        "error":
        "Internal server error"

    }), 500


# =========================
# START SERVER
# =========================

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

        debug=False
    )