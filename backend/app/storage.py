from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import shutil
import time
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


def active_jobs_key(ip_hash: str) -> str:
    return f"active:{ip_hash}"


async def reserve_active_job(redis: Redis, ip_hash: str, job_id: str) -> bool:
    now = time.time()
    expires_at = now + settings.job_timeout_seconds + 300
    reserved = await redis.eval(
        """
        local key_type = redis.call('TYPE', KEYS[1])['ok']
        if key_type == 'string' then
            local existing_job = redis.call('GET', KEYS[1])
            local remaining_ttl = redis.call('TTL', KEYS[1])
            redis.call('DEL', KEYS[1])
            if existing_job then
                redis.call('ZADD', KEYS[1], ARGV[1] + math.max(remaining_ttl, 1), existing_job)
            end
        elseif key_type ~= 'none' and key_type ~= 'zset' then
            return 0
        end
        redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
        if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
            return 0
        end
        redis.call('ZADD', KEYS[1], ARGV[3], ARGV[4])
        redis.call('EXPIRE', KEYS[1], ARGV[5])
        return 1
        """,
        1,
        active_jobs_key(ip_hash),
        now,
        settings.max_active_jobs_per_ip,
        expires_at,
        job_id,
        settings.job_timeout_seconds + 300,
    )
    return bool(reserved)


async def release_active_job(redis: Redis, ip_hash: str, job_id: str) -> None:
    await redis.eval(
        """
        local key_type = redis.call('TYPE', KEYS[1])['ok']
        if key_type == 'string' then
            if redis.call('GET', KEYS[1]) == ARGV[1] then
                redis.call('DEL', KEYS[1])
            end
            return 1
        end
        if key_type ~= 'zset' then
            return 1
        end
        redis.call('ZREM', KEYS[1], ARGV[1])
        if redis.call('ZCARD', KEYS[1]) == 0 then
            redis.call('DEL', KEYS[1])
        end
        return 1
        """,
        1,
        active_jobs_key(ip_hash),
        job_id,
    )


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
    await redis.zadd("job-expirations", {job_id: expires.timestamp()})
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
