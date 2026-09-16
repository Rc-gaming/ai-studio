from flask import Flask, request, jsonify, send_file, session
from huggingface_hub import InferenceClient, get_token
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from gradio_client import Client

import sqlite3
import os
import shutil
import urllib.request


app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "my-ai-studio-secret-key-2026"
)

DATABASE = "users.db"

HF_TOKEN = os.getenv("HF_TOKEN") or get_token()

client = InferenceClient(
    api_key=HF_TOKEN
)


# ==========================================
# CREDIT COSTS
# ==========================================

IMAGE_COST = 5
AUDIO_COST = 8
VIDEO_COST = 21
GENERATE_ALL_COST = 34


# ==========================================
# DATABASE CONNECTION
# ==========================================

def get_db():

    connection = sqlite3.connect(
        DATABASE,
        timeout=30,
        check_same_thread=False
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA busy_timeout = 30000"
    )

    connection.execute(
        "PRAGMA journal_mode = WAL"
    )

    connection.execute(
        "PRAGMA synchronous = NORMAL"
    )

    return connection


# ==========================================
# DATABASE INITIALIZATION
# ==========================================

def init_database():

    connection = get_db()

    try:

        cursor = connection.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                credits INTEGER DEFAULT 100
            )
        """)

        cursor.execute(
            "PRAGMA table_info(users)"
        )

        columns = [
            row["name"]
            for row in cursor.fetchall()
        ]

        if "credits" not in columns:

            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN credits INTEGER DEFAULT 100
            """)

        connection.commit()

    finally:

        connection.close()


init_database()


# ==========================================
# GET USER
# ==========================================

def get_user(user_id):

    connection = get_db()

    try:

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id, username, credits
            FROM users
            WHERE id = ?
            """,
            (user_id,)
        )

        user = cursor.fetchone()

        if user:

            return (
                user["id"],
                user["username"],
                user["credits"]
            )

        return None

    finally:

        connection.close()


# ==========================================
# GET CREDITS
# ==========================================

def get_credits(user_id):

    user = get_user(user_id)

    if user:

        return user[2]

    return 0


# ==========================================
# USE CREDITS
# ==========================================

def use_credits(user_id, amount):

    connection = get_db()

    try:

        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE users
            SET credits = credits - ?
            WHERE id = ?
            AND credits >= ?
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

        connection.commit()

        return True

    except Exception:

        connection.rollback()

        raise

    finally:

        connection.close()


# ==========================================
# ADD CREDITS
# ==========================================

def add_credits(user_id, amount):

    connection = get_db()

    try:

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

    except Exception:

        connection.rollback()

        raise

    finally:

        connection.close()


# ==========================================
# LOGIN REQUIRED
# ==========================================

def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:

            return jsonify({
                "error": "Please login first"
            }), 401

        return function(
            *args,
            **kwargs
        )

    return wrapper


# ==========================================
# HOME
# ==========================================

@app.route("/")
def home():

    return send_file("index.html")


# ==========================================
# REGISTER
# ==========================================

@app.route(
    "/register",
    methods=["POST"]
)
def register():

    connection = None

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
                "error":
                "Username and password are required"
            }), 400

        if len(password) < 6:

            return jsonify({
                "error":
                "Password must be at least 6 characters"
            }), 400

        password_hash = generate_password_hash(
            password
        )

        connection = get_db()

        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO users
            (username, password, credits)
            VALUES (?, ?, ?)
            """,
            (
                username,
                password_hash,
                100
            )
        )

        connection.commit()

        return jsonify({

            "message":
            "Registration successful",

            "credits":
            100

        })

    except sqlite3.IntegrityError:

        if connection:

            connection.rollback()

        return jsonify({
            "error":
            "Username already exists"
        }), 409

    except Exception as e:

        if connection:

            connection.rollback()

        print(
            "REGISTER ERROR:",
            str(e)
        )

        return jsonify({
            "error":
            str(e)
        }), 500

    finally:

        if connection:

            connection.close()


# ==========================================
# LOGIN
# ==========================================

@app.route(
    "/login",
    methods=["POST"]
)
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

        if not username or not password:

            return jsonify({
                "error":
                "Username and password are required"
            }), 400

        connection = get_db()

        try:

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

        finally:

            connection.close()

        if not user:

            return jsonify({
                "error":
                "Invalid username or password"
            }), 401

        user_id = user["id"]
        stored_username = user["username"]
        stored_password = user["password"]
        credits = user["credits"]

        if not check_password_hash(
            stored_password,
            password
        ):

            return jsonify({
                "error":
                "Invalid username or password"
            }), 401

        session["user_id"] = user_id
        session["username"] = stored_username

        return jsonify({

            "message":
            "Login successful",

            "username":
            stored_username,

            "credits":
            credits

        })

    except Exception as e:

        print(
            "LOGIN ERROR:",
            str(e)
        )

        return jsonify({
            "error":
            str(e)
        }), 500


# ==========================================
# CHECK LOGIN
# ==========================================

@app.route("/me")
def me():

    if "user_id" in session:

        return jsonify({

            "logged_in":
            True,

            "username":
            session.get("username")

        })

    return jsonify({
        "logged_in":
        False
    })


# ==========================================
# CREDITS
# ==========================================

@app.route("/credits")
@login_required
def credits():

    user_id = session["user_id"]

    return jsonify({

        "credits":
        get_credits(user_id)

    })


# ==========================================
# LOGOUT
# ==========================================

@app.route(
    "/logout",
    methods=["POST"]
)
def logout():

    session.clear()

    return jsonify({

        "message":
        "Logged out successfully"

    })


# ==========================================
# RECHARGE
# ==========================================

@app.route(
    "/recharge",
    methods=["POST"]
)
@login_required
def recharge():

    return jsonify({

        "error":
        "Recharge is currently unavailable. Payment system is not connected yet."

    }), 403


# ==========================================
# IMAGE GENERATION
# ==========================================

@app.route(
    "/generate",
    methods=["POST"]
)
@login_required
def generate():

    user_id = session["user_id"]

    if not use_credits(
        user_id,
        IMAGE_COST
    ):

        return jsonify({

            "error":
            "Your credits are finished. Please recharge to continue."

        }), 402

    try:

        data = request.get_json() or {}

        prompt = data.get(
            "prompt",
            ""
        ).strip()

        if not prompt:

            add_credits(
                user_id,
                IMAGE_COST
            )

            return jsonify({

                "error":
                "Please enter a prompt"

            }), 400

        print(
            "Starting image generation..."
        )

        client_image = Client(
            "black-forest-labs/FLUX.1-schnell",
            token=HF_TOKEN
        )

        result = client_image.predict(

            prompt,

            0,

            True,

            1024,

            1024,

            4,

            api_name="/infer"
        )

        image_path = result[0]

        from PIL import Image

        image = Image.open(
            image_path
        )

        image.save(
            "generated.png"
        )

        print(
            "Image generation completed."
        )

        return jsonify({

            "image_url":
            "/generated.png",

            "credits":
            get_credits(user_id)

        })

    except Exception as e:

        add_credits(
            user_id,
            IMAGE_COST
        )

        print(
            "IMAGE ERROR:",
            str(e)
        )

        return jsonify({

            "error":
            str(e)

        }), 500


# ==========================================
# IMAGE FILE
# ==========================================

@app.route(
    "/generated.png"
)
def generated_image():

    if not os.path.exists(
        "generated.png"
    ):

        return jsonify({
            "error":
            "Image not found"
        }), 404

    return send_file(
        "generated.png",
        mimetype="image/png"
    )


# ==========================================
# AUDIO GENERATION
# ==========================================

@app.route(
    "/audio",
    methods=["POST"]
)
@login_required
def audio():

    user_id = session["user_id"]

    if not use_credits(
        user_id,
        AUDIO_COST
    ):

        return jsonify({

            "error":
            "Your credits are finished. Please recharge to continue."

        }), 402

    try:

        data = request.get_json() or {}

        text = data.get(
            "text",
            ""
        ).strip()

        if not text:

            add_credits(
                user_id,
                AUDIO_COST
            )

            return jsonify({

                "error":
                "Please enter text"

            }), 400

        print(
            "Starting audio generation..."
        )

        client_audio = Client(
            "Remsky/Kokoro-TTS-Zero",
            token=HF_TOKEN
        )

        result = client_audio.predict(

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

        print(
            "Audio generation completed."
        )

        return jsonify({

            "audio_url":
            "/generated.wav",

            "credits":
            get_credits(user_id)

        })

    except Exception as e:

        add_credits(
            user_id,
            AUDIO_COST
        )

        print(
            "AUDIO ERROR:",
            str(e)
        )

        return jsonify({

            "error":
            str(e)

        }), 500


# ==========================================
# AUDIO FILE
# ==========================================

@app.route(
    "/generated.wav"
)
def generated_audio():

    if not os.path.exists(
        "generated.wav"
    ):

        return jsonify({
            "error":
            "Audio not found"
        }), 404

    return send_file(
        "generated.wav",
        mimetype="audio/wav"
    )
# ==========================================
# VIDEO PATH EXTRACTOR
# ==========================================

def extract_video_path(value):

    print(
        "VIDEO VALUE TYPE:",
        type(value)
    )

    print(
        "VIDEO VALUE:",
        value
    )

    # String path
    if isinstance(value, str):

        return value

    # Gradio dictionary
    if isinstance(value, dict):

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

    # Path object
    if hasattr(value, "__fspath__"):

        return os.fspath(value)

    # FileData object
    if hasattr(value, "path"):

        return value.path

    if hasattr(value, "url"):

        return value.url

    return None


# ==========================================
# VIDEO GENERATION
# ==========================================

@app.route(
    "/video",
    methods=["POST"]
)
@login_required
def video():

    user_id = session["user_id"]

    if not use_credits(
        user_id,
        VIDEO_COST
    ):

        return jsonify({

            "error":
            "Your credits are finished. Please recharge to continue."

        }), 402

    try:

        data = request.get_json() or {}

        prompt = data.get(
            "prompt",
            ""
        ).strip()

        if not prompt:

            add_credits(
                user_id,
                VIDEO_COST
            )

            return jsonify({

                "error":
                "Please enter a prompt"

            }), 400

        print()
        print("==========================================")
        print("STARTING HIGH QUALITY VIDEO GENERATION")
        print("==========================================")
        print("Prompt:", prompt)

        video_client = Client(
            "Lightricks/ltx-video-distilled",
            token=HF_TOKEN
        )

        # ======================================
        # HIGH QUALITY SETTINGS
        # ======================================

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

        print()
        print("==========================================")
        print("RAW VIDEO RESULT")
        print("==========================================")
        print(result)

        if not result:

            raise Exception(
                "Video generation returned an empty result."
            )

        video_output = result[0]

        video_path = extract_video_path(
            video_output
        )

        print()
        print("==========================================")
        print("EXTRACTED VIDEO PATH")
        print("==========================================")
        print(video_path)

        if not video_path:

            raise Exception(
                "Could not find video file in Gradio result."
            )

        # ======================================
        # LOCAL VIDEO
        # ======================================

        if os.path.exists(video_path):

            shutil.copyfile(
                video_path,
                "generated.mp4"
            )

        # ======================================
        # REMOTE VIDEO URL
        # ======================================

        elif isinstance(
            video_path,
            str
        ) and video_path.startswith("http"):

            print(
                "Downloading generated video..."
            )

            urllib.request.urlretrieve(
                video_path,
                "generated.mp4"
            )

        else:

            raise Exception(
                "Video file does not exist: "
                + str(video_path)
            )

        # ======================================
        # CHECK FILE
        # ======================================

        if not os.path.exists(
            "generated.mp4"
        ):

            raise Exception(
                "generated.mp4 was not created."
            )

        file_size = os.path.getsize(
            "generated.mp4"
        )

        if file_size <= 0:

            raise Exception(
                "Generated video file is empty."
            )

        print()
        print("==========================================")
        print("VIDEO GENERATION SUCCESS")
        print("==========================================")
        print(
            "Video size:",
            file_size,
            "bytes"
        )
        print(
            "Duration target: 5 seconds"
        )
        print(
            "Resolution: 768 x 512"
        )
        print(
            "Improve Texture: ON"
        )
        print("==========================================")

        return jsonify({

            "video_url":
            "/generated.mp4",

            "credits":
            get_credits(user_id)

        })

    except Exception as e:

        # Generation fail होने पर credits वापस
        try:

            add_credits(
                user_id,
                VIDEO_COST
            )

        except Exception as credit_error:

            print(
                "CREDIT REFUND ERROR:",
                str(credit_error)
            )

        print()
        print("==========================================")
        print("VIDEO ERROR")
        print("==========================================")
        print(repr(e))
        print("==========================================")

        return jsonify({

            "error":
            str(e)

        }), 500


# ==========================================
# VIDEO FILE
# ==========================================

@app.route(
    "/generated.mp4"
)
def generated_video():

    if not os.path.exists(
        "generated.mp4"
    ):

        return jsonify({

            "error":
            "Video not found"

        }), 404

    return send_file(
        "generated.mp4",
        mimetype="video/mp4"
    )


# ==========================================
# START SERVER
# ==========================================

if __name__ == "__main__":

    print()
    print("==========================================")
    print("AI STUDIO SERVER STARTING")
    print("==========================================")
    print("Open: http://127.0.0.1:5000")
    print("==========================================")

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )