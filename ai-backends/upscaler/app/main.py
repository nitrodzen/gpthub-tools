"""Persistent model pool with bounded GPU tiles and a backwards-compatible API."""
import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path

import numpy as np
import onnxruntime as ort
import pillow_heif
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps
from spandrel import ImageModelDescriptor, ModelLoader

LOG = logging.getLogger(__name__)
MODEL_DIR = Path(os.getenv('MODEL_DIR', '/models'))
MAX_UPLOAD = int(os.getenv('MAX_UPLOAD_BYTES', 52428800))
MAX_INPUT = int(os.getenv('MAX_INPUT_PIXELS', 100000000))
MAX_OUTPUT = int(os.getenv('MAX_OUTPUT_PIXELS', 200000000))
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
LOCK = threading.Lock()
MODELS = {}
LOAD_TIMES = {}
SPECS = {
    'standard-2': ('RealESRGAN_x2plus.pth', 2, 256),
    'standard-4': ('RealESRGAN_x4plus.pth', 4, 256),
    'photo': ('photo.safetensors', 2, 192),
    'web-photo': ('web-photo.safetensors', 4, 128),
    'detail': ('detail.pth', 4, 128),
    'illustration': ('illustration.pth', 4, 256),
    'clean': ('clean.onnx', 1, 256),
}
pillow_heif.register_heif_opener()
Image.MAX_IMAGE_PIXELS = MAX_INPUT
torch.set_num_threads(2)


@asynccontextmanager
async def lifespan(app):
    if os.getenv('REQUIRE_CUDA', 'true') == 'true' and DEVICE.type != 'cuda':
        raise RuntimeError('GPU unavailable; refusing to silently start CPU upscaling')
    for name, (filename, _, _) in SPECS.items():
        started = time.monotonic()
        if filename.endswith('.onnx'):
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            engine = ort.InferenceSession(str(MODEL_DIR / filename), sess_options=options,
                                          providers=['CPUExecutionProvider'])
        else:
            engine = ModelLoader().load_from_file(MODEL_DIR / filename)
            if not isinstance(engine, ImageModelDescriptor):
                raise RuntimeError(f'Not an image model: {name}')
            engine = engine.to(DEVICE).eval()
            if os.getenv('MODEL_PRECISION', 'fp32') == 'fp16' and engine.supports_half:
                engine.half()
        MODELS[name] = engine
        LOAD_TIMES[name] = round(time.monotonic() - started, 3)
        LOG.warning('Loaded %s in %.2fs on %s', name, LOAD_TIMES[name], DEVICE)
    yield
    MODELS.clear()


app = FastAPI(title='GPTHub Image Lab', lifespan=lifespan)


@app.get('/health')
def health():
    gpu_ok = DEVICE.type == 'cuda' and torch.cuda.is_available()
    # Allocate a tiny tensor to detect a lost runtime device, not just a cached flag.
    if gpu_ok:
        try:
            torch.zeros(1, device=DEVICE).cpu()
        except RuntimeError:
            gpu_ok = False
    ready = gpu_ok or (DEVICE.type == 'cpu' and os.getenv('REQUIRE_CUDA', 'true') != 'true')
    if not ready:
        raise HTTPException(503, 'GPU unavailable')
    return {'status': 'ok' if ready else 'degraded', 'device': str(DEVICE),
            'models': list(MODELS), 'loadSeconds': LOAD_TIMES,
            'residentMiB': round(torch.cuda.memory_allocated() / 1048576) if gpu_ok else 0}


def resolve_model(name, scale):
    key = f'standard-{scale}' if name == 'standard' else name
    if key not in SPECS or (name == 'photo' and scale != 2):
        raise HTTPException(400, 'Unsupported model or scale')
    if (key == 'clean' and scale != 1) or (key != 'clean' and scale not in (2, 4)):
        raise HTTPException(400, 'Unsupported model or scale')
    return key


def decode(raw):
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.width * source.height > MAX_INPUT:
                raise HTTPException(413, 'Image too large')
            source.load()
            return ImageOps.exif_transpose(source).convert('RGBA' if 'A' in source.getbands()
                       or 'transparency' in source.info else 'RGB')
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, 'Invalid image') from exc


def infer_tile(image, engine):
    data = np.asarray(image, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    if isinstance(engine, ort.InferenceSession):
        result = engine.run(None, {engine.get_inputs()[0].name: data})[0][0]
    else:
        tensor = torch.from_numpy(data).to(device=DEVICE, dtype=engine.dtype)
        with torch.inference_mode():
            result = engine(tensor)[0].cpu().float().numpy()
    return Image.fromarray(np.rint(np.clip(result.transpose(1, 2, 0), 0, 1) * 255).astype('uint8'))


def process(image, key, scale, strength):
    width, height = image.size
    if width * height * scale * scale > MAX_OUTPUT:
        raise HTTPException(413, 'Image too large for selected scale')
    _, native_scale, tile = SPECS[key]
    rgb = image.convert('RGB')
    result = Image.new('RGB', (width * scale, height * scale))
    pad = 32
    with LOCK:
        engine = MODELS[key]
        for y in range(0, height, tile):
            for x in range(0, width, tile):
                right, bottom = min(x + tile, width), min(y + tile, height)
                left_pad, top_pad = max(0, x - pad), max(0, y - pad)
                patch = rgb.crop((left_pad, top_pad, min(width, right + pad), min(height, bottom + pad)))
                output = infer_tile(patch, engine)
                output = output.crop(((x-left_pad)*native_scale, (y-top_pad)*native_scale,
                                      (right-left_pad)*native_scale, (bottom-top_pad)*native_scale))
                size = ((right-x)*scale, (bottom-y)*scale)
                if output.size != size:
                    output = output.resize(size, Image.Resampling.LANCZOS)
                if strength < 100:
                    original = rgb.crop((x,y,right,bottom)).resize(size, Image.Resampling.LANCZOS)
                    output = Image.blend(original, output, strength / 100)
                result.paste(output, (x*scale, y*scale))
        if DEVICE.type == 'cuda':
            torch.cuda.synchronize()
    if 'A' in image.getbands():
        result.putalpha(image.getchannel('A').resize(result.size, Image.Resampling.LANCZOS))
    return result


@app.post('/upscale')
async def upscale(file: UploadFile = File(...), scale: int = Form(4),
                  format: str = Form('png'), model: str = Form('standard'),
                  strength: int = Form(100)):
    key = resolve_model(model, scale)
    if format not in ('png', 'jpeg', 'jpg', 'webp') or not 0 <= strength <= 100:
        raise HTTPException(400, 'Invalid output options')
    try:
        raw = await file.read(MAX_UPLOAD + 1)
    finally:
        await file.close()
    if not raw or len(raw) > MAX_UPLOAD:
        raise HTTPException(413, 'Upload exceeds limit')
    image = await asyncio.to_thread(decode, raw)
    started = time.monotonic()
    try:
        result = await asyncio.to_thread(process, image, key, scale, strength)
    except torch.cuda.OutOfMemoryError as exc:
        raise HTTPException(413, 'GPU out of memory') from exc
    buffer = BytesIO()
    fmt = 'JPEG' if format in ('jpeg', 'jpg') else format.upper()
    if fmt == 'JPEG' and result.mode == 'RGBA':
        background = Image.new('RGB', result.size, 'white')
        background.paste(result, mask=result.getchannel('A'))
        result = background
    await asyncio.to_thread(result.save, buffer, format=fmt)
    elapsed = time.monotonic() - started
    return Response(buffer.getvalue(), media_type=f'image/{fmt.lower()}',
                    headers={'X-Processing-Seconds': f'{elapsed:.3f}', 'X-Model': key})
