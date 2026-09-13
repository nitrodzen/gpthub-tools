"""Native 1x restoration on CPU; the caller serializes it with background removal."""
import io
import os
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageOps

SESSION = None


def enhance_image(stream, strength, fmt):
    global SESSION
    with Image.open(stream) as source:
        source.load()
        original = ImageOps.exif_transpose(source).convert('RGBA')
    if SESSION is None:
        options = ort.SessionOptions()
        options.intra_op_num_threads = int(os.getenv('OMP_NUM_THREADS', '3'))
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        SESSION = ort.InferenceSession(str(Path(os.getenv('U2NET_HOME', '/models')) / 'clean.onnx'),
                                       options, providers=['CPUExecutionProvider'])
    rgb = original.convert('RGB')
    width, height = rgb.size
    result = Image.new('RGB', rgb.size)
    tile, pad = 256, 32
    for y in range(0, height, tile):
        for x in range(0, width, tile):
            right, bottom = min(width, x + tile), min(height, y + tile)
            left, top = max(0, x - pad), max(0, y - pad)
            patch = rgb.crop((left, top, min(width, right + pad), min(height, bottom + pad)))
            tensor = np.asarray(patch, dtype=np.float32).transpose(2, 0, 1)[None] / 255
            pixels = SESSION.run(None, {SESSION.get_inputs()[0].name: tensor})[0][0]
            output = Image.fromarray(np.rint(np.clip(pixels.transpose(1, 2, 0), 0, 1) * 255).astype('uint8'))
            output = output.crop((x - left, y - top, right - left, bottom - top))
            if strength < 100:
                output = Image.blend(rgb.crop((x, y, right, bottom)), output, strength / 100)
            result.paste(output, (x, y))
    result.putalpha(original.getchannel('A'))
    if fmt == 'JPEG':
        canvas = Image.new('RGB', result.size, 'white')
        canvas.paste(result, mask=result.getchannel('A'))
        result = canvas
    buffer = io.BytesIO()
    result.save(buffer, format=fmt)
    return buffer.getvalue()
