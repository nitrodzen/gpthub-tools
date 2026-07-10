from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    jobs_root: Path = Path(os.getenv("JOBS_ROOT", "/data/jobs"))
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "https://tools.gpthub.ru")
    app_secret: str = os.getenv("APP_SECRET", "development-only-change-me")
    upscale_url: str = os.getenv("UPSCALE_URL", "https://api.gpthub.ru/upscale")
    background_url: str = os.getenv("BACKGROUND_URL", "https://api.gpthub.ru/bgr1/process")
    clamav_host: str = os.getenv("CLAMAV_HOST", "clamav")
    clamav_port: int = int(os.getenv("CLAMAV_PORT", "3310"))
    clamav_required: bool = _bool("CLAMAV_REQUIRED", True)
    result_ttl_seconds: int = int(os.getenv("RESULT_TTL_SECONDS", "3600"))
    job_ttl_seconds: int = int(os.getenv("JOB_TTL_SECONDS", "7200"))
    max_image_bytes: int = int(os.getenv("MAX_IMAGE_BYTES", str(50 * 1024 * 1024)))
    max_document_bytes: int = int(os.getenv("MAX_DOCUMENT_BYTES", str(100 * 1024 * 1024)))
    max_job_bytes: int = int(os.getenv("MAX_JOB_BYTES", str(250 * 1024 * 1024)))
    max_result_bytes: int = int(os.getenv("MAX_RESULT_BYTES", str(500 * 1024 * 1024)))
    max_files: int = int(os.getenv("MAX_FILES", "20"))
    max_pdf_pages: int = int(os.getenv("MAX_PDF_PAGES", "250"))
    max_image_pixels: int = int(os.getenv("MAX_IMAGE_PIXELS", "100000000"))
    job_timeout_seconds: int = int(os.getenv("JOB_TIMEOUT_SECONDS", "1800"))


settings = Settings()
