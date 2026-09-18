import os
import time
import base64
import sqlite3
import threading
import wave
import requests

from flask import (
    Flask,
    request,
    jsonify,
    send_file,
    send_from_directory,
    session
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from functools import wraps


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "my-ai-studio-secret-key-2026"
)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY",
    ""
).strip()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    ""
).strip()

DATABASE = "users.db"


# =========================================================
# GEMINI MODELS
# =========================================================

IMAGE_MODEL = "gemini-3.1-flash-image"

AUDIO_MODEL = "gemini-3.1-flash-tts-preview"

VIDEO_MODEL = "veo-3.1-generate-preview"


# =========================================================
# CREDITS
# =========================================================

STARTING_CREDITS = 100

IMAGE_COST = 5
AUDIO_COST = 8
VIDEO_COST = 21
GENERATE_ALL_COST = 34


# =========================================================
# VIDEO JOBS
# =========================================================

video_jobs = {}


# =========================================================
# DATABASE
# =========================================================

def is_postgres():
    return bool(DATABASE_URL)


def get_db():

    if is_postgres():

        import psycopg2

        return psycopg2.connect(
            DATABASE_URL
        )

    conn = sqlite3.connect(
        DATABASE,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    return conn


def db_query(
    query,
    params=(),
    fetchone=False,
    fetchall=False
):

    conn = get_db()

    try:

        if is_postgres():
            query = query.replace(
                "?",
                "%s"
            )

        cur = conn.cursor()

        cur.execute(
            query,
            params
        )

        result = None

        if fetchone:

            row = cur.fetchone()

            if row:

                if is_postgres():

                    columns = [
                        description[0]
                        for description in cur.description
                    ]

                    result = dict(
                        zip(
                            columns,
                            row
                        )
                    )

                else:

                    result = dict(row)

        elif fetchall:

            rows = cur.fetchall()

            if is_postgres():

                columns = [
                    description[0]
                    for description in cur.description
                ]

                result = [
                    dict(
                        zip(
                            columns,
                            row
                        )
                    )
                    for row in rows
                ]

            else:

                result = [
                    dict(row)
                    for row in rows
                ]

        conn.commit()

        return result

    except Exception:

        conn.rollback()

        raise

    finally:

        conn.close()


def init_db():

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
                ADD COLUMN IF NOT EXISTS credits
                INTEGER NOT NULL DEFAULT 100
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

            cur.execute(
                "PRAGMA table_info(users)"
            )

            columns = [
                row[1]
                for row in cur.fetchall()
            ]

            if "credits" not in columns:

                cur.execute("""
                    ALTER TABLE users
                    ADD COLUMN credits
                    INTEGER NOT NULL DEFAULT 100
                """)

        conn.commit()

    finally:

        conn.close()


init_db()


# =========================================================
# AUTH
# =========================================================

def login_required(func):

    @wraps(func)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:

            return jsonify({
                "error": "Please login first."
            }), 401

        return func(
            *args,
            **kwargs
        )

    return wrapper


def current_user():

    user_id = session.get(
        "user_id"
    )

    if not user_id:
        return None

    return db_query(
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

    user = db_query(
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

    return int(
        user["credits"]
    )


def change_credits(
    user_id,
    amount
):

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

    except Exception:

        conn.rollback()

        raise

    finally:

        conn.close()


def use_credits(
    user_id,
    amount
):

    conn = get_db()

    try:

        cur = conn.cursor()

        if is_postgres():

            cur.execute(
                """
                SELECT credits
                FROM users
                WHERE id = %s
                FOR UPDATE
                """,
                (user_id,)
            )

        else:

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

        current = int(
            row[0]
        )

        if current < amount:

            conn.rollback()

            return False, current

        new_balance = (
            current - amount
        )

        if is_postgres():

            cur.execute(
                """
                UPDATE users
                SET credits = %s
                WHERE id = %s
                """,
                (
                    new_balance,
                    user_id
                )
            )

        else:

            cur.execute(
                """
                UPDATE users
                SET credits = ?
                WHERE id = ?
                """,
                (
                    new_balance,
                    user_id
                )
            )

        conn.commit()

        return True, new_balance

    except Exception:

        conn.rollback()

        raise

    finally:

        conn.close()


# =========================================================
# GEMINI
# =========================================================

def gemini_headers():

    return {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json"
    }


def gemini_ready():

    return bool(
        GEMINI_API_KEY
    )


# =========================================================
# HOMEPAGE
# =========================================================

@app.route("/")
def home():

    return send_from_directory(
        os.path.dirname(
            os.path.abspath(__file__)
        ),
        "index.html"
    )


# =========================================================
# HEALTH
# =========================================================

@app.route("/health")
def health():

    return jsonify({

        "status": "ok",

        "database": (
            "postgresql"
            if is_postgres()
            else "sqlite"
        ),

        "gemini_configured": (
            gemini_ready()
        ),

        "image_model": IMAGE_MODEL,

        "audio_model": AUDIO_MODEL,

        "video_model": VIDEO_MODEL
    })


# =========================================================
# REGISTER
# =========================================================

@app.route(
    "/register",
    methods=["POST"]
)
def register():

    try:

        data = (
            request.get_json(
                silent=True
            )
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

        existing = db_query(
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
                "error":
                    "Username already exists."
            }), 409

        password_hash = (
            generate_password_hash(
                password
            )
        )

        db_query(
            """
            INSERT INTO users
            (username, password, credits)
            VALUES (?, ?, ?)
            """,
            (
                username,
                password_hash,
                STARTING_CREDITS
            )
        )

        return jsonify({

            "success": True,

            "message":
                "Registration successful.",

            "credits":
                STARTING_CREDITS
        })

    except Exception as e:

        print(
            "REGISTER ERROR:",
            str(e)
        )

        return jsonify({
            "error":
                "Registration failed."
        }), 500


# =========================================================
# LOGIN
# =========================================================

@app.route(
    "/login",
    methods=["POST"]
)
def login():

    try:

        data = (
            request.get_json(
                silent=True
            )
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

        user = db_query(
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
                "error":
                    "Invalid username or password."
            }), 401

        if not check_password_hash(
            user["password"],
            password
        ):

            return jsonify({
                "error":
                    "Invalid username or password."
            }), 401

        session["user_id"] = user["id"]

        session["username"] = (
            user["username"]
        )

        return jsonify({

            "success": True,

            "username":
                user["username"],

            "credits":
                int(
                    user["credits"]
                )
        })

    except Exception as e:

        print(
            "LOGIN ERROR:",
            str(e)
        )

        return jsonify({
            "error":
                "Login failed."
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

            "id":
                user["id"],

            "username":
                user["username"],

            "credits":
                int(
                    user["credits"]
                )
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

    balance = get_user_credits(
        session["user_id"]
    )

    return jsonify({

        "credits":
            balance,

        "costs": {

            "image":
                IMAGE_COST,

            "audio":
                AUDIO_COST,

            "video":
                VIDEO_COST,

            "generate_all":
                GENERATE_ALL_COST
        }
    })
    # =========================================================
# IMAGE GENERATION
# =========================================================

@app.route(
    "/generate",
    methods=["POST"]
)
@login_required
def generate():

    user_id = session["user_id"]

    if not gemini_ready():

        return jsonify({
            "error":
                "GEMINI_API_KEY is not configured."
        }), 500

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

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

        success, balance = use_credits(
            user_id,
            IMAGE_COST
        )

        if not success:

            return jsonify({
                "error":
                    "Your credits are finished. Please recharge to continue.",
                "credits":
                    balance
            }), 402

        # -------------------------------------------------
        # IMPORTANT
        #
        # We intentionally keep the request simple.
        # No mime_type is sent here.
        #
        # Google official REST example supports:
        # model + input
        #
        # output_image.data contains the image.
        # -------------------------------------------------

        payload = {

            "model":
                IMAGE_MODEL,

            "input":
                prompt
        }

        response = requests.post(

            f"{BASE_URL}/interactions",

            headers=
                gemini_headers(),

            json=
                payload,

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

            change_credits(
                user_id,
                IMAGE_COST
            )

            try:

                error_data = (
                    response.json()
                )

            except Exception:

                error_data = (
                    response.text
                )

            return jsonify({

                "error":
                    "Image generation failed.",

                "details":
                    error_data
            }), response.status_code

        result = response.json()

        image_data = None

        # -------------------------------------------------
        # Preferred current output
        # -------------------------------------------------

        output_image = (
            result.get(
                "output_image"
            )
        )

        if isinstance(
            output_image,
            dict
        ):

            image_data = (
                output_image.get(
                    "data"
                )
            )

        # -------------------------------------------------
        # Fallback: search steps
        # -------------------------------------------------

        if not image_data:

            steps = result.get(
                "steps",
                []
            )

            if isinstance(
                steps,
                list
            ):

                for step in steps:

                    if not isinstance(
                        step,
                        dict
                    ):
                        continue

                    content = step.get(
                        "content",
                        []
                    )

                    if not isinstance(
                        content,
                        list
                    ):
                        continue

                    for block in content:

                        if not isinstance(
                            block,
                            dict
                        ):
                            continue

                        if (
                            block.get(
                                "type"
                            )
                            == "image"
                        ):

                            image_data = (
                                block.get(
                                    "data"
                                )
                            )

                            if image_data:
                                break

                    if image_data:
                        break

        if not image_data:

            change_credits(
                user_id,
                IMAGE_COST
            )

            return jsonify({

                "error":
                    "Google returned no image data.",

                "response":
                    result
            }), 500

        try:

            image_bytes = (
                base64.b64decode(
                    image_data
                )
            )

        except Exception as e:

            change_credits(
                user_id,
                IMAGE_COST
            )

            return jsonify({

                "error":
                    "Could not decode image.",

                "details":
                    str(e)
            }), 500

        with open(
            "generated.png",
            "wb"
        ) as f:

            f.write(
                image_bytes
            )

        return jsonify({

            "success": True,

            "image_url":
                "/generated.png",

            "credits":
                get_user_credits(
                    user_id
                )
        })

    except requests.exceptions.Timeout:

        change_credits(
            user_id,
            IMAGE_COST
        )

        return jsonify({
            "error":
                "Image generation timed out."
        }), 504

    except Exception as e:

        print(
            "IMAGE ERROR:",
            str(e)
        )

        change_credits(
            user_id,
            IMAGE_COST
        )

        return jsonify({

            "error":
                "Image generation failed.",

            "details":
                str(e)
        }), 500


# =========================================================
# GENERATED IMAGE
# =========================================================

@app.route("/generated.png")
def generated_image():

    if not os.path.exists(
        "generated.png"
    ):

        return jsonify({
            "error":
                "No generated image found."
        }), 404

    return send_file(
        "generated.png",
        mimetype="image/png"
    )


# =========================================================
# AUDIO
# =========================================================

@app.route(
    "/audio",
    methods=["POST"]
)
@login_required
def audio():

    user_id = session["user_id"]

    if not gemini_ready():

        return jsonify({
            "error":
                "GEMINI_API_KEY is not configured."
        }), 500

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        text = str(
            data.get(
                "text",
                ""
            )
        ).strip()

        if not text:

            text = str(
                data.get(
                    "prompt",
                    ""
                )
            ).strip()

        if not text:

            return jsonify({
                "error":
                    "Please enter text."
            }), 400

        success, balance = use_credits(
            user_id,
            AUDIO_COST
        )

        if not success:

            return jsonify({

                "error":
                    "Your credits are finished. Please recharge to continue.",

                "credits":
                    balance
            }), 402

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

            f"{BASE_URL}/interactions",

            headers=
                gemini_headers(),

            json=
                payload,

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

            change_credits(
                user_id,
                AUDIO_COST
            )

            try:

                error_data = (
                    response.json()
                )

            except Exception:

                error_data = (
                    response.text
                )

            return jsonify({

                "error":
                    "Audio generation failed.",

                "details":
                    error_data
            }), response.status_code

        result = response.json()

        audio_data = None

        output_audio = (
            result.get(
                "output_audio"
            )
        )

        if isinstance(
            output_audio,
            dict
        ):

            audio_data = (
                output_audio.get(
                    "data"
                )
            )

        if not audio_data:

            change_credits(
                user_id,
                AUDIO_COST
            )

            return jsonify({

                "error":
                    "Google returned no audio data.",

                "response":
                    result
            }), 500

        pcm_bytes = (
            base64.b64decode(
                audio_data
            )
        )

        save_pcm_as_wav(
            pcm_bytes,
            "generated.wav"
        )

        return jsonify({

            "success":
                True,

            "audio_url":
                "/generated.wav",

            "credits":
                get_user_credits(
                    user_id
                )
        })

    except requests.exceptions.Timeout:

        change_credits(
            user_id,
            AUDIO_COST
        )

        return jsonify({
            "error":
                "Audio generation timed out."
        }), 504

    except Exception as e:

        print(
            "AUDIO ERROR:",
            str(e)
        )

        change_credits(
            user_id,
            AUDIO_COST
        )

        return jsonify({

            "error":
                "Audio generation failed.",

            "details":
                str(e)
        }), 500


# =========================================================
# PCM -> WAV
# =========================================================

def save_pcm_as_wav(
    pcm_data,
    filename
):

    with wave.open(
        filename,
        "wb"
    ) as wf:

        wf.setnchannels(
            1
        )

        wf.setsampwidth(
            2
        )

        wf.setframerate(
            24000
        )

        wf.writeframes(
            pcm_data
        )


# =========================================================
# GENERATED AUDIO
# =========================================================

@app.route("/generated.wav")
def generated_audio():

    if not os.path.exists(
        "generated.wav"
    ):

        return jsonify({
            "error":
                "No generated audio found."
        }), 404

    return send_file(
        "generated.wav",
        mimetype="audio/wav"
    )


# =========================================================
# VIDEO START
# =========================================================

@app.route(
    "/video",
    methods=["POST"]
)
@login_required
def video():

    user_id = session["user_id"]

    if not gemini_ready():

        return jsonify({
            "error":
                "GEMINI_API_KEY is not configured."
        }), 500

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

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

        success, balance = use_credits(
            user_id,
            VIDEO_COST
        )

        if not success:

            return jsonify({

                "error":
                    "Your credits are finished. Please recharge to continue.",

                "credits":
                    balance
            }), 402

        job_id = (
            str(
                int(
                    time.time() * 1000
                )
            )
            + "_"
            + str(user_id)
        )

        video_jobs[job_id] = {

            "status":
                "starting",

            "progress":
                5,

            "video_url":
                None,

            "error":
                None
        }

        thread = threading.Thread(

            target=
                generate_video_job,

            args=(
                job_id,
                user_id,
                prompt
            ),

            daemon=True
        )

        thread.start()

        return jsonify({

            "success":
                True,

            "job_id":
                job_id,

            "credits":
                get_user_credits(
                    user_id
                )
        })

    except Exception as e:

        print(
            "VIDEO ERROR:",
            str(e)
        )

        return jsonify({

            "error":
                "Video generation failed.",

            "details":
                str(e)
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

            "status":
                "generating",

            "progress":
                10,

            "video_url":
                None,

            "error":
                None
        }

        payload = {

            "instances": [

                {
                    "prompt":
                        prompt
                }
            ]
        }

        response = requests.post(

            f"{BASE_URL}/models/"
            f"{VIDEO_MODEL}:predictLongRunning",

            headers=
                gemini_headers(),

            json=
                payload,

            timeout=120
        )

        print(
            "VIDEO START:",
            response.status_code
        )

        print(
            "VIDEO RESPONSE:",
            response.text[:3000]
        )

        if response.status_code != 200:

            change_credits(
                user_id,
                VIDEO_COST
            )

            video_jobs[job_id] = {

                "status":
                    "failed",

                "progress":
                    0,

                "video_url":
                    None,

                "error":
                    response.text
            }

            return

        operation = (
            response.json()
        )

        operation_name = (
            operation.get(
                "name"
            )
        )

        if not operation_name:

            change_credits(
                user_id,
                VIDEO_COST
            )

            video_jobs[job_id] = {

                "status":
                    "failed",

                "progress":
                    0,

                "video_url":
                    None,

                "error":
                    "Google did not return an operation name."
            }

            return

        # -------------------------------------------------
        # POLL
        # -------------------------------------------------

        for attempt in range(
            120
        ):

            time.sleep(
                10
            )

            status_response = requests.get(

                f"{BASE_URL}/"
                f"{operation_name}",

                headers={

                    "x-goog-api-key":
                        GEMINI_API_KEY
                },

                timeout=60
            )

            if status_response.status_code != 200:

                change_credits(
                    user_id,
                    VIDEO_COST
                )

                video_jobs[job_id] = {

                    "status":
                        "failed",

                    "progress":
                        0,

                    "video_url":
                        None,

                    "error":
                        status_response.text
                }

                return

            status_data = (
                status_response.json()
            )

            if status_data.get(
                "done"
            ) is True:

                if "error" in status_data:

                    change_credits(
                        user_id,
                        VIDEO_COST
                    )

                    video_jobs[job_id] = {

                        "status":
                            "failed",

                        "progress":
                            0,

                        "video_url":
                            None,

                        "error":
                            str(
                                status_data["error"]
                            )
                    }

                    return

                video_uri = (
                    extract_video_uri(
                        status_data
                    )
                )

                if not video_uri:

                    change_credits(
                        user_id,
                        VIDEO_COST
                    )

                    video_jobs[job_id] = {

                        "status":
                            "failed",

                        "progress":
                            0,

                        "video_url":
                            None,

                        "error":
                            "Google returned no video URL."
                    }

                    return

                download_response = requests.get(

                    video_uri,

                    headers={

                        "x-goog-api-key":
                            GEMINI_API_KEY
                    },

                    timeout=180,

                    allow_redirects=True
                )

                if download_response.status_code != 200:

                    change_credits(
                        user_id,
                        VIDEO_COST
                    )

                    video_jobs[job_id] = {

                        "status":
                            "failed",

                        "progress":
                            0,

                        "video_url":
                            None,

                        "error":
                            "Could not download video."
                    }

                    return

                with open(
                    "generated.mp4",
                    "wb"
                ) as f:

                    f.write(
                        download_response.content
                    )

                video_jobs[job_id] = {

                    "status":
                        "completed",

                    "progress":
                        100,

                    "video_url":
                        "/generated.mp4",

                    "error":
                        None
                }

                return

            progress = min(
                95,
                10 + attempt
            )

            video_jobs[job_id] = {

                "status":
                    "generating",

                "progress":
                    progress,

                "video_url":
                    None,

                "error":
                    None
            }

        change_credits(
            user_id,
            VIDEO_COST
        )

        video_jobs[job_id] = {

            "status":
                "failed",

            "progress":
                0,

            "video_url":
                None,

            "error":
                "Video generation timed out."
        }

    except Exception as e:

        print(
            "VIDEO JOB ERROR:",
            str(e)
        )

        change_credits(
            user_id,
            VIDEO_COST
        )

        video_jobs[job_id] = {

            "status":
                "failed",

            "progress":
                0,

            "video_url":
                None,

            "error":
                str(e)
        }


# =========================================================
# EXTRACT VIDEO URL
# =========================================================

def extract_video_uri(
    data
):

    try:

        response = data.get(
            "response",
            {}
        )

        generate_response = (
            response.get(
                "generateVideoResponse",
                {}
            )
        )

        samples = (
            generate_response.get(
                "generatedSamples",
                []
            )
        )

        if samples:

            video = (
                samples[0]
                .get(
                    "video",
                    {}
                )
            )

            uri = (
                video.get(
                    "uri"
                )
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
    "/video/status/<job_id>"
)
@login_required
def video_status(
    job_id
):

    job = video_jobs.get(
        job_id
    )

    if not job:

        return jsonify({
            "error":
                "Video job not found."
        }), 404

    return jsonify({

        "status":
            job.get(
                "status"
            ),

        "progress":
            job.get(
                "progress",
                0
            ),

        "video_url":
            job.get(
                "video_url"
            ),

        "error":
            job.get(
                "error"
            )
    })


# =========================================================
# GENERATED VIDEO
# =========================================================

@app.route("/generated.mp4")
def generated_video():

    if not os.path.exists(
        "generated.mp4"
    ):

        return jsonify({
            "error":
                "No generated video found."
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

        "error":
            "Recharge is currently unavailable. "
            "Payment system is not connected yet."
    }), 403


# =========================================================
# RUN
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