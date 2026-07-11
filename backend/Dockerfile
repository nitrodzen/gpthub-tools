FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp/app-home

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl tini libreoffice-writer libreoffice-core \
      fonts-crosextra-caladea fonts-crosextra-carlito fonts-liberation2 \
      fonts-noto-core fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --home-dir /home/app app

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --upgrade pip && pip install -r requirements.txt
COPY app ./app

RUN mkdir -p /data/jobs /data/metrics /tmp/app-home && chown -R app:app /data /tmp/app-home /app
USER app

EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
