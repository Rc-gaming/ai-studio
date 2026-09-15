from flask import Flask, request, jsonify, send_file, session
from huggingface_hub import InferenceClient, get_token
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sqlite3
import shutil


app = Flask(__name__)

app.secret_key = "my-ai-studio-secret-key-2026"

DATABASE = "users.db"

NEW_USER_CREDITS = 100

IMAGE_COST = 5
AUDIO_COST = 8
VIDEO_COST = 21


# =========================
# HUGGING FACE
# =========================

client = InferenceClient(
    api_key=get_token()
)


# =========================
# DATABASE
# =========================

def init_database():

    connection = sqlite3.connect(DATABASE)

    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            credits INTEGER NOT NULL DEFAULT 100
        )
    """)

    try:
        cursor.execute(
            "ALTER TABLE users ADD COLUMN credits INTEGER NOT NULL DEFAULT 100"
        )
    except sqlite3.OperationalError:
        pass

    connection.commit()
    connection.close()


init_database()


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
# GET CREDITS
# =========================

def get_credits(user_id):

    connection = sqlite3.connect(DATABASE)

    cursor = connection.cursor()

    cursor.execute(
        "SELECT credits FROM users WHERE id = ?",
        (user_id,)
    )

    result = cursor.fetchone()

    connection.close()

    if result is None:
        return 0

    return result[0]


# =========================
# ADD CREDITS
# =========================

def add_credits(user_id, amount):

    connection = sqlite3.connect(DATABASE)

    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE users
        SET credits = credits + ?
        WHERE id = ?
        """,
        (amount, user_id)
    )

    connection.commit()

    connection.close()


# =========================
# USE CREDITS
# =========================

def use_credits(user_id, amount):

    connection = sqlite3.connect(DATABASE)

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

    success = cursor.rowcount == 1

    connection.commit()

    connection.close()

    return success


# =========================
# HOME
# =========================

@app.route("/")
def home():

    return send_file("index.html")


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

        connection = sqlite3.connect(
            DATABASE
        )

        cursor = connection.cursor()

        hashed_password = generate_password_hash(
            password
        )

        cursor.execute(
            """
            INSERT INTO users
            (
                username,
                password,
                credits
            )
            VALUES (?, ?, ?)
            """,
            (
                username,
                hashed_password,
                NEW_USER_CREDITS
            )
        )

        connection.commit()

        connection.close()

        return jsonify({
            "message":
            "Account created successfully",

            "credits":
            NEW_USER_CREDITS
        })

    except sqlite3.IntegrityError:

        return jsonify({
            "error":
            "Username already exists"
        }), 400

    except Exception as e:

        print(
            "REGISTER ERROR:",
            str(e)
        )

        return jsonify({
            "error":
            str(e)
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

        if not username or not password:

            return jsonify({
                "error":
                "Username and password are required"
            }), 400

        connection = sqlite3.connect(
            DATABASE
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                username,
                password,
                credits
            FROM users
            WHERE username = ?
            """,
            (username,)
        )

        user = cursor.fetchone()

        connection.close()

        if user is None:

            return jsonify({
                "error":
                "Username or password is incorrect"
            }), 401

        user_id = user[0]

        saved_username = user[1]

        saved_password = user[2]

        credits = user[3]

        if not check_password_hash(
            saved_password,
            password
        ):

            return jsonify({
                "error":
                "Username or password is incorrect"
            }), 401

        session["user_id"] = user_id

        session["username"] = saved_username

        return jsonify({
            "message":
            "Login successful",

            "username":
            saved_username,

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


# =========================
# CHECK LOGIN
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
# DEMO RECHARGE
# =========================

@app.route("/recharge", methods=["POST"])
@login_required
def recharge():

    try:

        data = request.get_json() or {}

        amount = int(
            data.get(
                "amount",
                0
            )
        )

        allowed_amounts = [
            100,
            500,
            1000
        ]

        if amount not in allowed_amounts:

            return jsonify({
                "error":
                "Invalid recharge amount"
            }), 400

        add_credits(
            session["user_id"],
            amount
        )

        return jsonify({

            "message":
            "Credits added successfully",

            "credits":
            get_credits(
                session["user_id"]
            )

        })

    except Exception as e:

        return jsonify({
            "error":
            str(e)
        }), 500


# =========================
# LOGOUT
# =========================

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


# =========================
# IMAGE
# =========================

@app.route(
    "/generate",
    methods=["POST"]
)
@login_required
def generate():

    user_id = session["user_id"]

    if get_credits(user_id) < IMAGE_COST:

        return jsonify({
            "error":
            "Your credits are finished. Please recharge to continue."
        }), 402

    try:

        from gradio_client import Client
        from PIL import Image

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

        client_image = Client(
            "black-forest-labs/FLUX.1-schnell"
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

        image = Image.open(
            image_path
        )

        image.save(
            "generated.png"
        )

        use_credits(
            user_id,
            IMAGE_COST
        )

        return jsonify({

            "image_url":
            "/generated.png",

            "credits":
            get_credits(user_id)

        })

    except Exception as e:

        print(
            "IMAGE ERROR:",
            str(e)
        )

        return jsonify({
            "error":
            str(e)
        }), 500


# =========================
# IMAGE FILE
# =========================

@app.route("/generated.png")
def generated_image():

    return send_file(
        "generated.png",
        mimetype="image/png"
    )


# =========================
# AUDIO
# =========================

@app.route(
    "/audio",
    methods=["POST"]
)
@login_required
def audio():

    user_id = session["user_id"]

    if get_credits(user_id) < AUDIO_COST:

        return jsonify({
            "error":
            "Your credits are finished. Please recharge to continue."
        }), 402

    try:

        from gradio_client import Client

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

        client_audio = Client(
            "Remsky/Kokoro-TTS-Zero"
        )

        result = client_audio.predict(

            text,

            ["af_heart"],

            1,

            api_name=
            "/generate_speech_from_ui"
        )

        audio_path = result[0]

        shutil.copy(
            audio_path,
            "generated.wav"
        )

        use_credits(
            user_id,
            AUDIO_COST
        )

        return jsonify({

            "audio_url":
            "/generated.wav",

            "credits":
            get_credits(user_id)

        })

    except Exception as e:

        print(
            "AUDIO ERROR:",
            str(e)
        )

        return jsonify({
            "error":
            str(e)
        }), 500


# =========================
# AUDIO FILE
# =========================

@app.route("/generated.wav")
def generated_audio():

    return send_file(
        "generated.wav",
        mimetype="audio/wav"
    )


# =========================
# VIDEO
# =========================

@app.route(
    "/video",
    methods=["POST"]
)
@login_required
def video():

    user_id = session["user_id"]

    if get_credits(user_id) < VIDEO_COST:

        return jsonify({
            "error":
            "Your credits are finished. Please recharge to continue."
        }), 402

    try:

        from gradio_client import Client

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

        video_client = Client(
            "OpenKing/wan2-video-generation"
        )

        result = video_client.predict(

            prompt,

            None,

            1280,

            704,

            73,

            35,

            5.0,

            -1,

            api_name="/generate_video"
        )

        video_path = result[0]

        shutil.copy(
            video_path,
            "generated.mp4"
        )

        use_credits(
            user_id,
            VIDEO_COST
        )

        return jsonify({

            "video_url":
            "/generated.mp4",

            "credits":
            get_credits(user_id)

        })

    except Exception as e:

        print(
            "VIDEO ERROR:",
            str(e)
        )

        return jsonify({
            "error":
            str(e)
        }), 500


# =========================
# VIDEO FILE
# =========================

@app.route("/generated.mp4")
def generated_video():

    return send_file(
        "generated.mp4",
        mimetype="video/mp4"
    )


# =========================
# START
# =========================

if __name__ == "__main__":

    app.run(
        debug=True
    )