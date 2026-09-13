import base64
import gc
import io
import os
import threading
import warnings
import urllib.request
import uuid
import sys
import ctypes

import numpy as np
import onnxruntime as ort
import pillow_heif
from flask import Flask, Response, jsonify, request
from PIL import Image, ImageOps
from rembg import remove
from rembg.session_factory import sessions_class

os.environ.setdefault('OMP_NUM_THREADS', '4')
pillow_heif.register_heif_opener()
Image.MAX_IMAGE_PIXELS = 100_000_000
warnings.simplefilter('error', Image.DecompressionBombWarning)
PRESETS = {'fast': ('birefnet-general-lite', 1024),
           'quality': ('birefnet-general', 1024),
           'portrait': ('birefnet-portrait', 1024)}
LOCK = threading.Lock()
SESSIONS = {}


def release_memory():
    gc.collect()
    if sys.platform == 'linux':
        ctypes.CDLL('libc.so.6').malloc_trim(0)


def create_session(model):
    options = ort.SessionOptions()
    options.intra_op_num_threads = int(os.environ['OMP_NUM_THREADS'])
    options.inter_op_num_threads = 1
    # Full BiRefNet otherwise retains several GB of temporary activation buffers.
    options.enable_cpu_mem_arena = False
    options.enable_mem_pattern = False
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    session_class = next(cls for cls in sessions_class if cls.name() == model)
    return session_class(model, options, ['CPUExecutionProvider'])


# ONNX sessions retain large activation arenas. Keep only the active preset.
SESSIONS['fast'] = create_session(PRESETS['fast'][0])
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024


@app.get('/health')
def health():
    return {'status': 'ok', 'presets': list(PRESETS), 'loaded': list(SESSIONS)}


@app.post('/enhance')
def enhance():
    from enhance import enhance_image
    uploaded = request.files.get('file')
    fmt = request.form.get('format', 'png')
    try:
        strength = int(request.form.get('strength', 100))
        if uploaded is None or not 0 <= strength <= 100 or fmt not in ('png', 'webp', 'jpeg', 'jpg'):
            return jsonify({'error': 'Invalid image or options'}), 400
        fmt = 'JPEG' if fmt in ('jpeg', 'jpg') else fmt.upper()
        with LOCK:
            SESSIONS.clear()
            release_memory()
            data = enhance_image(uploaded.stream, strength, fmt)
        return Response(data, mimetype=f'image/{fmt.lower()}')
    except (ValueError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return jsonify({'error': 'Invalid image'}), 400
    except Exception:
        app.logger.exception('Image enhancement failed')
        return jsonify({'error': 'Image enhancement failed'}), 500


@app.post('/process')
def process():
    uploaded = request.files.get('file')
    preset = request.form.get('preset', 'quality')
    if uploaded is None or preset not in PRESETS:
        return jsonify({'error': 'Invalid image or preset'}), 400
    try:
        remote = os.getenv('REMOTE_BACKGROUND_URL', '')
        if remote and preset != 'fast':
            boundary = uuid.uuid4().hex
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="preset"\r\n\r\n{preset}'
                    f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="image"'
                    '\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
            body += uploaded.read() + f'\r\n--{boundary}--\r\n'.encode()
            forwarded = urllib.request.Request(remote, data=body,
                headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
            with urllib.request.urlopen(forwarded, timeout=600) as response:
                return Response(response.read(), mimetype='application/json')
        with Image.open(uploaded.stream) as opened:
            opened.load()
            original = ImageOps.exif_transpose(opened).convert('RGBA')
        small = original.copy()
        small.thumbnail((PRESETS[preset][1], PRESETS[preset][1]), Image.Resampling.LANCZOS)
        with LOCK:
            enhancement = sys.modules.get('enhance')
            if enhancement is not None and enhancement.SESSION is not None:
                enhancement.SESSION = None
                release_memory()
            if preset not in SESSIONS:
                SESSIONS.clear()
                release_memory()
                SESSIONS[preset] = create_session(PRESETS[preset][0])
            masked = remove(small, session=SESSIONS[preset], only_mask=True)
        alpha = masked.resize(original.size, Image.Resampling.LANCZOS)
        result = np.array(original)
        result[..., 3] = np.rint(result[..., 3].astype(np.float32) * (np.array(alpha) / 255)).astype(np.uint8)
        buffer = io.BytesIO()
        Image.fromarray(result).save(buffer, format='PNG')
        return jsonify({'result_image': base64.b64encode(buffer.getvalue()).decode('ascii')})
    except (ValueError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return jsonify({'error': 'Invalid image'}), 400
    except Exception:
        app.logger.exception('Background removal failed')
        return jsonify({'error': 'Background removal failed'}), 500
