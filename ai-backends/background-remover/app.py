import base64
import io
import logging
import os
import warnings

import numpy as np
import pillow_heif
from flask import Flask, jsonify, request
from PIL import Image, ImageOps, UnidentifiedImageError
from rembg import new_session, remove
from werkzeug.exceptions import RequestEntityTooLarge

LOGGER = logging.getLogger("gpthub_tools.background_remover")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


MAX_UPLOAD_BYTES = env_int("MAX_UPLOAD_BYTES", 50 * 1024 * 1024)
MAX_INPUT_PIXELS = env_int("MAX_INPUT_PIXELS", 100_000_000)
PROCESS_MAX_SIDE = env_int("PROCESS_MAX_SIDE", 512)

Image.MAX_IMAGE_PIXELS = MAX_INPUT_PIXELS
warnings.simplefilter("error", Image.DecompressionBombWarning)
pillow_heif.register_heif_opener()
SESSION = new_session(os.getenv("REMBG_MODEL", "birefnet-general-lite"))


def resize_for_mask(image: Image.Image) -> Image.Image:
    width, height = image.size
    if max(width, height) <= PROCESS_MAX_SIDE:
        return image
    ratio = PROCESS_MAX_SIDE / max(width, height)
    return image.resize((int(width * ratio), int(height * ratio)), Image.Resampling.LANCZOS)


def open_image() -> Image.Image:
    uploaded = request.files.get("file")
    if uploaded is None:
        raise ValueError("missing file")
    try:
        with Image.open(uploaded.stream) as source:
            if source.format not in {"PNG", "JPEG", "WEBP", "HEIF", "TIFF", "BMP"}:
                raise ValueError("unsupported image format")
            source.load()
            return ImageOps.exif_transpose(source).convert("RGBA")
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise ValueError("invalid image") from exc


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_: RequestEntityTooLarge):
        return jsonify({"error": "Image exceeds the upload limit"}), 413

    @app.post("/process")
    def process_image():
        try:
            original = open_image()
            masked = remove(resize_for_mask(original), session=SESSION)
            alpha = masked.getchannel("A").resize(original.size, Image.Resampling.BILINEAR)
            result = np.array(original)
            result[..., 3] = (result[..., 3].astype(np.float32) * (np.array(alpha) / 255)).astype(
                np.uint8
            )

            buffer = io.BytesIO()
            Image.fromarray(result).save(buffer, format="PNG")
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            return jsonify({"result_image": encoded})
        except ValueError:
            return jsonify({"error": "Invalid or unsupported image file"}), 400
        except Exception:
            LOGGER.exception("Background removal failed")
            return jsonify({"error": "Background removal failed"}), 500

    return app


app = create_app()
