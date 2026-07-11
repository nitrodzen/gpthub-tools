FROM pytorch/pytorch:2.0.1-cuda11.7-cudnn8-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --home-dir /home/app app
WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && pip install -r requirements.txt

# Real-ESRGAN expands its complete tiled output to float32 before moving it to
# system RAM. For large images that creates an unnecessary second GPU-sized
# allocation. Move the FP16 tensor first, then expand it on the CPU.
RUN python -c "from importlib.util import find_spec; from pathlib import Path; spec=find_spec('realesrgan'); path=Path(next(iter(spec.submodule_search_locations))) / 'utils.py'; source=path.read_text(); old='.data.squeeze().float().cpu().clamp_(0, 1).numpy()'; new='.data.squeeze().cpu().float().clamp_(0, 1).numpy()'; assert old in source; path.write_text(source.replace(old, new))"

COPY app ./app
RUN mkdir -p /models && chown -R app:app /app /models

USER app
EXPOSE 5011
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5011"]
