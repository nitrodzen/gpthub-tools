FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    U2NET_HOME=/models \
    NUMBA_CACHE_DIR=/tmp/numba

RUN useradd --uid 10001 --create-home --home-dir /home/app app
WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && pip install -r requirements.txt

COPY app.py ./
RUN mkdir -p /models && chown -R app:app /app /models

USER app
EXPOSE 5010
CMD ["gunicorn", "--workers", "1", "--threads", "1", "--bind", "0.0.0.0:5010", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
