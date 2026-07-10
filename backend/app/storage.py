from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from redis.asyncio import Redis

from .config import settings
from .models import ApiError, ErrorCode, JobStatus, JobView, Operation


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def token_digest(token: str) -> str:
    return hmac.new(settings.app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def ip_digest(ip: str) -> str:
    return hmac.new(settings.app_secret.encode(), ip.encode(), hashlib.sha256).hexdigest()[:24]


def job_path(job_id: str) -> Path:
    return settings.jobs_root / job_id


def create_job_directory(job_id: str) -> Path:
    root = job_path(job_id)
    (root / "input").mkdir(parents=True, exist_ok=False)
    (root / "output").mkdir(mode=0o700)
    return root


def delete_job_directory(job_id: str) -> None:
    shutil.rmtree(job_path(job_id), ignore_errors=True)


async def create_job_record(
    redis: Redis,
    *,
    job_id: str,
    token: str,
    operation: Operation,
    ip_hash: str,
    files: list[dict[str, Any]],
    options: dict[str, Any],
) -> dict[str, str]:
    created = utcnow()
    expires = created + timedelta(seconds=settings.result_ttl_seconds)
    record = {
        "job_id": job_id,
        "token_hash": token_digest(token),
        "operation": operation.value,
        "status": JobStatus.QUEUED.value,
        "progress": "0",
        "total": str(len(files)),
        "created_at": iso(created),
        "expires_at": iso(expires),
        "ip_hash": ip_hash,
        "files": json.dumps(files, ensure_ascii=False),
        "options": json.dumps(options, ensure_ascii=False),
    }
    await redis.hset(f"job:{job_id}", mapping=record)
    await redis.expire(f"job:{job_id}", settings.job_ttl_seconds)
    await redis.set(f"active:{ip_hash}", job_id, ex=settings.job_timeout_seconds + 300)
    return record


async def get_job_record(redis: Redis, job_id: str) -> dict[str, str] | None:
    record = await redis.hgetall(f"job:{job_id}")
    return record or None


def authorized(record: dict[str, str], token: str) -> bool:
    expected = record.get("token_hash", "")
    return bool(expected) and hmac.compare_digest(expected, token_digest(token))


def record_to_view(record: dict[str, str]) -> JobView:
    error = None
    if record.get("error_code"):
        details = json.loads(record["error_details"]) if record.get("error_details") else None
        error = ApiError(
            code=ErrorCode(record["error_code"]),
            message=record.get("error_message", ""),
            details=details,
        )
    return JobView(
        jobId=record["job_id"],
        operation=Operation(record["operation"]),
        status=JobStatus(record["status"]),
        progress=int(record.get("progress", "0")),
        total=int(record.get("total", "0")),
        createdAt=record["created_at"],
        expiresAt=record["expires_at"],
        resultName=record.get("result_name"),
        resultType=record.get("result_type"),
        error=error,
    )


def new_capability() -> tuple[str, str]:
    return secrets.token_urlsafe(24), secrets.token_urlsafe(32)
