from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import shutil
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from redis.asyncio import Redis

from .config import settings
from .metrics import metrics
from .models import AI_OPERATIONS, ErrorCode, JobCreated, JobFailure, JobStatus, JobView, Operation
from .security import (
    IMAGE_EXTENSIONS,
    allowed_extensions,
    scan_file,
    validate_signature,
    validate_upscale_dimensions,
)
from .storage import (
    authorized,
    create_job_directory,
    create_job_record,
    delete_job_directory,
    get_job_record,
    ip_digest,
    new_capability,
    record_to_view,
    release_active_job,
    reserve_active_job,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.jobs_root.mkdir(parents=True, exist_ok=True)
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.queue = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    await app.state.redis.ping()
    try:
        await metrics.initialize()
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Metrics store is unavailable during startup: %s", exc)
    yield
    await app.state.queue.close()
    await app.state.redis.aclose()


app = FastAPI(
    title="GPTHub Tools API",
    version=os.getenv("APP_VERSION", "dev"),
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


def error_response(
    code: ErrorCode, message: str, status: int, details: dict | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code.value, "message": message, "details": details}},
    )


@app.exception_handler(JobFailure)
async def job_failure_handler(_: Request, exc: JobFailure) -> JSONResponse:
    status = {
        ErrorCode.RATE_LIMITED: 429,
        ErrorCode.ACTIVE_JOB_EXISTS: 409,
        ErrorCode.FORBIDDEN: 403,
        ErrorCode.JOB_NOT_FOUND: 404,
        ErrorCode.SCANNER_UNAVAILABLE: 503,
        ErrorCode.UPSTREAM_ERROR: 502,
    }.get(exc.code, 400)
    return error_response(exc.code, exc.message, status, exc.details)


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(
        ErrorCode.INVALID_FILE, "The request is invalid", 422, {"errors": exc.errors()}
    )


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


async def consume_rate(redis: Redis, ip_hash: str, operation: Operation, units: int) -> None:
    window = 3600
    limit = 20 if operation in AI_OPERATIONS else 60
    key = f"rate:{'ai' if operation in AI_OPERATIONS else 'local'}:{ip_hash}"
    now = time.time()
    async with redis.pipeline(transaction=True) as pipeline:
        await pipeline.zremrangebyscore(key, 0, now - window)
        await pipeline.zcard(key)
        result = await pipeline.execute()
    current = int(result[1])
    if current + units > limit:
        raise JobFailure(ErrorCode.RATE_LIMITED, "The hourly processing limit has been reached")
    mapping = {f"{now}:{uuid.uuid4().hex}": now for _ in range(units)}
    await redis.zadd(key, mapping)
    await redis.expire(key, window + 60)


def sanitize_original(name: str | None, extension: str) -> str:
    base = Path(name or f"file{extension}").name
    safe = "".join(
        character if character.isalnum() or character in " ._-()" else "_" for character in base
    )
    return (safe[:150] or f"file{extension}").strip()


async def save_upload(upload: UploadFile, destination: Path, limit: int, remaining: int) -> int:
    written = 0
    with destination.open("wb") as target:
        while chunk := await upload.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                raise JobFailure(ErrorCode.FILE_TOO_LARGE, "A file exceeds the allowed size")
            if written > remaining:
                raise JobFailure(
                    ErrorCode.JOB_TOO_LARGE, "The total upload exceeds the allowed size"
                )
            target.write(chunk)
    if written == 0:
        raise JobFailure(ErrorCode.INVALID_FILE, "Empty files are not supported")
    return written


@app.get("/api/health")
async def health(request: Request) -> dict:
    redis: Redis = request.app.state.redis
    redis_ok = bool(await redis.ping())
    workers = 0
    async for _ in redis.scan_iter("worker-heartbeat:*"):
        workers += 1
    disk = shutil.disk_usage(settings.jobs_root)
    return {
        "status": "ok"
        if redis_ok and workers >= settings.expected_workers and disk.free > settings.max_job_bytes
        else "degraded",
        "version": app.version,
        "redis": redis_ok,
        "workers": workers,
        "expectedWorkers": settings.expected_workers,
        "freeBytes": disk.free,
    }


def metrics_period(
    from_value: str | None, to_value: str | None, bucket: str
) -> tuple[date, date] | None:
    if bucket not in {"hour", "day"}:
        return None
    try:
        today = datetime.now(UTC).date()
        to_date = date.fromisoformat(to_value) if to_value else today
        from_date = date.fromisoformat(from_value) if from_value else to_date - timedelta(days=6)
    except ValueError:
        return None
    days = (to_date - from_date).days + 1
    if days < 1 or days > settings.metrics_retention_days:
        return None
    if bucket == "hour" and days > 31:
        return None
    return from_date, to_date


async def metrics_live_snapshot(redis: Redis) -> dict[str, int | bool | str]:
    try:
        redis_ok = bool(await redis.ping())
    except Exception:
        redis_ok = False
    workers = 0
    if redis_ok:
        async for _ in redis.scan_iter("worker-heartbeat:*"):
            workers += 1
    disk = shutil.disk_usage(settings.jobs_root)
    return {
        "status": "ok"
        if redis_ok and workers >= settings.expected_workers and disk.free > settings.max_job_bytes
        else "degraded",
        "redis": redis_ok,
        "workers": workers,
        "expectedWorkers": settings.expected_workers,
        "freeBytes": disk.free,
    }


@app.get("/api/metrics")
async def get_metrics(
    request: Request,
    key: str | None = None,
    from_value: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    bucket: str = "day",
):
    headers = {"Cache-Control": "no-store"}
    if not (
        settings.metrics_api_key
        and key
        and hmac.compare_digest(key, settings.metrics_api_key)
    ):
        return Response(status_code=404, headers=headers)
    period = metrics_period(from_value, to, bucket)
    if not period:
        return JSONResponse(
            status_code=400,
            headers=headers,
            content={
                "error": {"code": "INVALID_METRICS_PERIOD", "message": "Invalid metrics period"}
            },
        )
    try:
        payload = await metrics.report(from_date=period[0], to_date=period[1], bucket=bucket)
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Metrics endpoint is unavailable: %s", exc)
        return JSONResponse(
            status_code=503,
            headers=headers,
            content={
                "error": {"code": "METRICS_UNAVAILABLE", "message": "Metrics are unavailable"}
            },
        )
    payload["live"] = await metrics_live_snapshot(request.app.state.redis)
    return JSONResponse(content=payload, headers=headers)


@app.post("/api/jobs/{operation}", response_model=JobCreated, status_code=202)
async def create_job(
    request: Request,
    operation: Operation,
    files: Annotated[list[UploadFile], File()],
    options: Annotated[str, Form()] = "{}",
) -> JobCreated:
    redis: Redis = request.app.state.redis
    if not files:
        await metrics.record_rejected(
            operation=operation,
            file_count=0,
            input_bytes=0,
            error_code=ErrorCode.INVALID_FILE.value,
        )
        raise JobFailure(ErrorCode.INVALID_FILE, "At least one file is required")
    if len(files) > settings.max_files:
        await metrics.record_rejected(
            operation=operation,
            file_count=len(files),
            input_bytes=0,
            error_code=ErrorCode.TOO_MANY_FILES.value,
        )
        raise JobFailure(ErrorCode.TOO_MANY_FILES, "Too many files in one job")
    try:
        parsed_options = json.loads(options)
        if not isinstance(parsed_options, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError) as exc:
        await metrics.record_rejected(
            operation=operation,
            file_count=len(files),
            input_bytes=0,
            error_code=ErrorCode.INVALID_FILE.value,
        )
        raise JobFailure(ErrorCode.INVALID_FILE, "Job options must be a JSON object") from exc

    ip_hash = ip_digest(client_ip(request))
    job_id, token = new_capability()
    if not await reserve_active_job(redis, ip_hash, job_id):
        await metrics.record_rejected(
            operation=operation,
            file_count=len(files),
            input_bytes=0,
            error_code=ErrorCode.ACTIVE_JOB_EXISTS.value,
        )
        raise JobFailure(
            ErrorCode.ACTIVE_JOB_EXISTS, "Maximum concurrent jobs reached for this address"
        )
    stored: list[dict] = []
    total = 0
    metrics_accepted = False
    try:
        await consume_rate(redis, ip_hash, operation, len(files))
        root = create_job_directory(job_id)
        allowed = allowed_extensions(operation)
        for upload in files:
            extension = Path(upload.filename or "").suffix.lower()
            if extension not in allowed:
                raise JobFailure(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    f"Unsupported file extension: {extension or 'none'}",
                )
            per_file_limit = (
                settings.max_image_bytes
                if extension in IMAGE_EXTENSIONS
                else settings.max_document_bytes
            )
            stored_path = root / "input" / f"{uuid.uuid4().hex}{extension}"
            written = await save_upload(
                upload, stored_path, per_file_limit, settings.max_job_bytes - total
            )
            total += written
            await asyncio.to_thread(validate_signature, stored_path, extension)
            if operation is Operation.UPSCALE:
                await asyncio.to_thread(
                    validate_upscale_dimensions,
                    stored_path,
                    parsed_options.get("scale", 2),
                )
            await scan_file(stored_path)
            stored.append(
                {
                    "path": str(stored_path),
                    "original": sanitize_original(upload.filename, extension),
                    "content_type": upload.content_type or "application/octet-stream",
                    "size": written,
                }
            )

        record = await create_job_record(
            redis,
            job_id=job_id,
            token=token,
            operation=operation,
            ip_hash=ip_hash,
            files=stored,
            options=parsed_options,
        )
        await metrics.record_accepted(
            job_id=job_id,
            operation=operation,
            file_count=len(stored),
            input_bytes=total,
        )
        metrics_accepted = True
        queue_name = "ai" if operation in AI_OPERATIONS else "local"
        queued = await request.app.state.queue.enqueue_job(
            "run_operation",
            job_id,
            _queue_name=queue_name,
            _job_id=job_id,
        )
        if queued is None:
            raise JobFailure(ErrorCode.INTERNAL_ERROR, "The job could not be queued")
        return JobCreated(
            jobId=job_id,
            token=token,
            status=JobStatus.QUEUED,
            expiresAt=record["expires_at"],
        )
    except JobFailure as exc:
        if metrics_accepted:
            await metrics.record_terminal(
                job_id=job_id, status=JobStatus.FAILED, error_code=exc.code.value
            )
        else:
            await metrics.record_rejected(
                operation=operation,
                file_count=len(files),
                input_bytes=total,
                error_code=exc.code.value,
            )
        delete_job_directory(job_id)
        await redis.delete(f"job:{job_id}")
        await release_active_job(redis, ip_hash, job_id)
        raise
    except Exception:
        if metrics_accepted:
            await metrics.record_terminal(
                job_id=job_id,
                status=JobStatus.FAILED,
                error_code=ErrorCode.INTERNAL_ERROR.value,
            )
        else:
            await metrics.record_rejected(
                operation=operation,
                file_count=len(files),
                input_bytes=total,
                error_code=ErrorCode.INTERNAL_ERROR.value,
            )
        delete_job_directory(job_id)
        await redis.delete(f"job:{job_id}")
        await release_active_job(redis, ip_hash, job_id)
        raise
    finally:
        for upload in files:
            await upload.close()


async def authorized_job(redis: Redis, job_id: str, token: str) -> dict[str, str]:
    record = await get_job_record(redis, job_id)
    if not record:
        raise JobFailure(ErrorCode.JOB_NOT_FOUND, "Job not found")
    if not authorized(record, token):
        raise JobFailure(ErrorCode.FORBIDDEN, "The job token is invalid")
    return record


@app.get("/api/jobs/{job_id}", response_model=JobView)
async def get_job(
    request: Request,
    job_id: str,
    token: Annotated[str, Header(alias="X-Capability-Token", min_length=16)],
) -> JobView:
    return record_to_view(await authorized_job(request.app.state.redis, job_id, token))


@app.get("/api/jobs/{job_id}/download")
async def download_job(
    request: Request,
    job_id: str,
    token: Annotated[str, Header(alias="X-Capability-Token", min_length=16)],
):
    record = await authorized_job(request.app.state.redis, job_id, token)
    if record["status"] != JobStatus.SUCCEEDED.value:
        raise JobFailure(ErrorCode.INVALID_FILE, "The result is not ready")
    result_path = Path(record.get("result_path", ""))
    expected_root = (settings.jobs_root / job_id / "output").resolve()
    try:
        resolved = result_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise JobFailure(ErrorCode.JOB_NOT_FOUND, "The result has expired") from exc
    if expected_root not in resolved.parents:
        raise JobFailure(ErrorCode.FORBIDDEN, "The result path is invalid")
    return FileResponse(
        resolved,
        media_type=record.get("result_type", "application/octet-stream"),
        filename=record.get("result_name", resolved.name),
    )


@app.delete("/api/jobs/{job_id}", status_code=204)
async def delete_job(
    request: Request,
    job_id: str,
    token: Annotated[str, Header(alias="X-Capability-Token", min_length=16)],
):
    redis: Redis = request.app.state.redis
    record = await authorized_job(redis, job_id, token)
    if record["status"] in {
        JobStatus.SUCCEEDED.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELLED.value,
    }:
        delete_job_directory(job_id)
        await redis.delete(f"job:{job_id}")
        await redis.zrem("job-expirations", job_id)
        return None
    await redis.hset(f"job:{job_id}", mapping={"status": JobStatus.CANCELLED.value})
    await metrics.record_terminal(job_id=job_id, status=JobStatus.CANCELLED)
    await redis.zrem("job-expirations", job_id)
    if record["status"] != JobStatus.RUNNING.value:
        delete_job_directory(job_id)
        await release_active_job(redis, record["ip_hash"], job_id)
    return None
