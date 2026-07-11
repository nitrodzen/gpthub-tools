import asyncio
import logging
import os
import warnings
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Final

import numpy as np
import pillow_heif
import torch
from basicsr.archs.rrdbnet_arch import RRDBNet
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps, UnidentifiedImageError
from realesrgan import RealESRGANer

LOGGER = logging.getLogger("gpthub_tools.upscaler")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


MAX_UPLOAD_BYTES: Final = env_int("MAX_UPLOAD_BYTES", 50 * 1024 * 1024)
MAX_INPUT_PIXELS: Final = env_int("MAX_INPUT_PIXELS", 100_000_000)
MAX_OUTPUT_PIXELS: Final = env_int("MAX_OUTPUT_PIXELS", 420_000_000)
TILE_SIZE: Final = env_int("TILE_SIZE", 512)
MODEL_DIR: Final = Path(os.getenv("MODEL_DIR", "/models"))
MODEL_FILES: Final = {
    2: "RealESRGAN_x2plus.pth",
    4: "RealESRGAN_x4plus.pth",
}

pillow_heif.register_heif_opener()
Image.MAX_IMAGE_PIXELS = MAX_INPUT_PIXELS
warnings.simplefilter("error", Image.DecompressionBombWarning)

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
UPSCALE_LOCK = asyncio.Lock()
UPSCALERS: dict[int, RealESRGANer] = {}


def load_upscalers() -> dict[int, RealESRGANer]:
    engines: dict[int, RealESRGANer] = {}
    for scale, filename in MODEL_FILES.items():
        model_path = MODEL_DIR / filename
        if not model_path.is_file():
            raise RuntimeError(f"Missing required model file: {filename}")

        model = RRDBNet(
            num_in_ch=3,
            num_out_ch=3,
            num_feat=64,
            num_block=23,
            num_grow_ch=32,
            scale=scale,
        )
        checkpoint = torch.load(model_path, map_location=DEVICE)
        if "params_ema" in checkpoint:
            checkpoint = checkpoint["params_ema"]
        elif "params" in checkpoint:
            checkpoint = checkpoint["params"]
        else:
            raise RuntimeError(f"Model {filename} has no params_ema or params payload")

        engine = RealESRGANer(
            scale=scale,
            model_path=str(model_path),
            model=model,
            tile=TILE_SIZE,
            tile_pad=10,
            pre_pad=0,
            half=torch.cuda.is_available(),
            device=DEVICE,
        )
        engine.model.load_state_dict(checkpoint, strict=True)
        engine.model.eval()
        engines[scale] = engine
        LOGGER.info("Real-ESRGAN x%s model loaded on %s", scale, DEVICE.type)
    return engines


@asynccontextmanager
async def lifespan(_: FastAPI):
    UPSCALERS.update(load_upscalers())
    yield
    UPSCALERS.clear()


app = FastAPI(title="GPTHub Tools AI Upscaler", lifespan=lifespan)


def decode_image(raw: bytes) -> Image.Image:
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP", "HEIF", "TIFF", "BMP"}:
                raise ValueError("unsupported image format")
            source.load()
            return ImageOps.exif_transpose(source).convert("RGB")
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=400, detail="Invalid or unsupported image file") from exc


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "device": DEVICE.type, "models": sorted(UPSCALERS)}


@app.post("/upscale")
async def upscale(
    file: UploadFile = File(...),
    scale: int = Form(4),
    format: str = Form("png"),
    quality: int = Form(100),
) -> Response:
    if scale not in UPSCALERS:
        raise HTTPException(status_code=400, detail="Scale must be 2 or 4")
    output_format = format.lower()
    if output_format not in {"png", "jpeg", "jpg"}:
        raise HTTPException(status_code=400, detail="Output format must be PNG or JPEG")

    try:
        raw = await file.read(MAX_UPLOAD_BYTES + 1)
    finally:
        await file.close()
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the upload limit")

    image = decode_image(raw)
    if image.width * image.height * scale * scale > MAX_OUTPUT_PIXELS:
        raise HTTPException(status_code=413, detail="Image is too large for the selected scale")
    try:
        async with UPSCALE_LOCK:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            output, _ = await asyncio.to_thread(
                UPSCALERS[scale].enhance,
                np.array(image),
                outscale=scale,
            )
    except Exception as exc:
        LOGGER.exception("Upscaling failed")
        raise HTTPException(status_code=500, detail="Upscaling failed") from exc

    result = Image.fromarray(output)
    buffer = BytesIO()
    if output_format in {"jpeg", "jpg"}:
        result.save(buffer, format="JPEG", quality=max(1, min(100, quality)))
        media_type = "image/jpeg"
    else:
        result.save(buffer, format="PNG")
        media_type = "image/png"
    return Response(content=buffer.getvalue(), media_type=media_type)
