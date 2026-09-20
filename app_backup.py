from flask import Flask, request, jsonify, send_file
from huggingface_hub import InferenceClient, get_token

app = Flask(__name__)
@app.route("/")
def home():
    return send_file("index.html")

client = InferenceClient(
    api_key=get_token()
)


# =========================
# TEXT TO IMAGE
# =========================

@app.route("/generate", methods=["POST"])
def generate():
    try:
        from gradio_client import Client
        from PIL import Image

        data = request.get_json()
        prompt = data.get("prompt", "")

        if not prompt:
            return jsonify({"error": "Please enter a prompt"}), 400

        client_image = Client("black-forest-labs/FLUX.1-schnell")

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

        image = Image.open(image_path)
        image.save("generated.png")

        return jsonify({
            "image_url": "/generated.png"
        })

    except Exception as e:
        print("IMAGE ERROR:", str(e))

        return jsonify({
            "error": str(e)
        }), 500


@app.route("/generated.png")
def generated_image():
    return send_file("generated.png")


# =========================
# TEXT TO AUDIO
# =========================

@app.route("/audio", methods=["POST"])
def audio():
    try:
        from gradio_client import Client
        import shutil

        data = request.get_json()
        text = data.get("text", "")

        if not text:
            return jsonify({"error": "Please enter text"}), 400

        client_audio = Client("Remsky/Kokoro-TTS-Zero")

        result = client_audio.predict(
            text,
            ["af_heart"],
            1,
            api_name="/generate_speech_from_ui"
        )

        audio_path = result[0]

        shutil.copy(audio_path, "generated.wav")

        return jsonify({
            "audio_url": "/generated.wav"
        })

    except Exception as e:
        print("AUDIO ERROR:", str(e))

        return jsonify({
            "error": str(e)
        }), 500


@app.route("/generated.wav")
def generated_audio():
    return send_file(
        "generated.wav",
        mimetype="audio/wav"
    )


# =========================
# TEXT TO VIDEO
# =========================

@app.route("/video", methods=["POST"])
def video():
    try:
        from gradio_client import Client
        import shutil

        data = request.get_json()
        prompt = data.get("prompt", "")

        if not prompt:
            return jsonify({"error": "Please enter a prompt"}), 400

        video_client = Client("OpenKing/wan2-video-generation")

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

        shutil.copy(video_path, "generated.mp4")

        return jsonify({
            "video_url": "/generated.mp4"
        })

    except Exception as e:
        print("VIDEO ERROR:", str(e))

        return jsonify({
            "error": str(e)
        }), 500
@app.route("/generated.mp4")
def generated_video():
    return send_file(
        "generated.mp4",
        mimetype="video/mp4"
    )


# =========================
# START SERVER
# =========================

if __name__ == "__main__":
    app.run(debug=True)