from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import time
import uuid

from arq import Retry
from arq.connections import RedisSettings

from .config import settings
from .metrics import metrics
from .models import ErrorCode, JobFailure, JobStatus, Operation
from .operations import execute, result_mime
from .storage import (
    active_jobs_key,
    delete_job_directory,
    job_expiration,
    release_active_job,
    result_expiration,
)

logger = logging.getLogger(__name__)

WORKER_BOOT_ID = uuid.uuid4().hex
WORKER_ID = (
    f"{os.getenv('ARQ_QUEUE', 'local')}:{socket.gethostname()}:{os.getpid()}:{WORKER_BOOT_ID}"
)
CLAIMED = 1
CLAIM_BUSY = 2
CLAIM_RETRY_SECONDS = 15


def decoded(value: bytes | str | None) -> str | None:
    return value.decode() if isinstance(value, bytes) else value


async def store_terminal_state(
    redis,
    key: str,
    job_id: str,
    run_token: str,
    mapping: dict[str, str],
) -> bool:
    expires_at, expires_score = result_expiration()
    mapping = {**mapping, "expires_at": expires_at}
    arguments: list[str] = []
    for field, value in mapping.items():
        arguments.extend((field, value))
    changed = await redis.eval(
        """
        if redis.call('HGET', KEYS[1], 'status') ~= ARGV[1] then
            return 0
        end
        if redis.call('HGET', KEYS[1], 'run_token') ~= ARGV[2] then
            return 0
        end
        redis.call('HSET', KEYS[1], unpack(ARGV, 6))
        redis.call('EXPIRE', KEYS[1], ARGV[3])
        redis.call('ZADD', KEYS[2], ARGV[4], ARGV[5])
        return 1
        """,
        2,
        key,
        "job-expirations",
        JobStatus.RUNNING.value,
        run_token,
        settings.result_ttl_seconds,
        expires_score,
        job_id,
        *arguments,
    )
    return bool(changed)


async def mark_job_running(redis, key: str, run_token: str, ip_hash: str) -> int:
    expires_at, _expires_score = job_expiration()
    active_score = time.time() + settings.job_timeout_seconds + 300
    result = await redis.eval(
        """
        local function renew_leases()
            redis.call('HSET', KEYS[1], 'expires_at', ARGV[6])
            redis.call('EXPIRE', KEYS[1], ARGV[7])
            local key_type = redis.call('TYPE', KEYS[2])['ok']
            if key_type == 'string' then
                local existing_job = redis.call('GET', KEYS[2])
                redis.call('DEL', KEYS[2])
                if existing_job then
                    redis.call('ZADD', KEYS[2], ARGV[8], existing_job)
                end
            elseif key_type ~= 'none' and key_type ~= 'zset' then
                return false
            end
            local existing_score = redis.call('ZSCORE', KEYS[2], ARGV[9])
            local score = tonumber(ARGV[8])
            if existing_score and tonumber(existing_score) > score then
                score = tonumber(existing_score)
            end
            redis.call('ZADD', KEYS[2], score, ARGV[9])
            redis.call('EXPIRE', KEYS[2], ARGV[7])
            return true
        end
        local status = redis.call('HGET', KEYS[1], 'status')
        if status == ARGV[1] then
            if not renew_leases() then
                return 0
            end
            redis.call(
                'HSET', KEYS[1],
                'status', ARGV[2],
                'progress', '0',
                'run_owner', ARGV[3],
                'run_token', ARGV[4]
            )
            return 1
        end
        if status == ARGV[2] then
            local previous_owner = redis.call('HGET', KEYS[1], 'run_owner')
            if previous_owner and previous_owner ~= ''
                and redis.call('EXISTS', ARGV[5] .. previous_owner) == 1 then
                return 2
            end
            if not renew_leases() then
                return 0
            end
            redis.call(
                'HSET', KEYS[1],
                'run_owner', ARGV[3],
                'run_token', ARGV[4]
            )
            return 1
        end
        return 0
        """,
        2,
        key,
        active_jobs_key(ip_hash),
        JobStatus.QUEUED.value,
        JobStatus.RUNNING.value,
        WORKER_ID,
        run_token,
        "worker-heartbeat:",
        expires_at,
        settings.job_ttl_seconds,
        active_score,
        key.removeprefix("job:"),
    )
    return int(result)


async def finish_job_claim(redis, key: str, run_token: str) -> str | None:
    status = await redis.eval(
        """
        if redis.call('HGET', KEYS[1], 'run_token') ~= ARGV[1] then
            return ''
        end
        local status = redis.call('HGET', KEYS[1], 'status') or ''
        redis.call('HDEL', KEYS[1], 'run_owner', 'run_token')
        return status
        """,
        1,
        key,
        run_token,
    )
    return decoded(status) or None


async def heartbeat(ctx: dict) -> None:
    await ctx["redis"].set(f"worker-heartbeat:{WORKER_ID}", "1", ex=90)


async def heartbeat_loop(ctx: dict) -> None:
    while True:
        await asyncio.sleep(30)
        await heartbeat(ctx)


async def on_startup(ctx: dict) -> None:
    await heartbeat(ctx)
    ctx["heartbeat_task"] = asyncio.create_task(heartbeat_loop(ctx))


async def on_shutdown(ctx: dict) -> None:
    task = ctx.get("heartbeat_task")
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await ctx["redis"].delete(f"worker-heartbeat:{WORKER_ID}")


async def run_operation(ctx: dict, job_id: str) -> None:
    redis = ctx["redis"]
    key = f"job:{job_id}"
    raw_record = await redis.hgetall(key)
    record = {
        key.decode() if isinstance(key, bytes) else key: value.decode()
        if isinstance(value, bytes)
        else value
        for key, value in raw_record.items()
    }
    if not record or record.get("status") == JobStatus.CANCELLED.value:
        return
    ip_hash = record["ip_hash"]
    root = settings.jobs_root / job_id
    run_token = uuid.uuid4().hex
    claim_result = await mark_job_running(redis, key, run_token, ip_hash)
    if claim_result == CLAIM_BUSY:
        await heartbeat(ctx)
        raise Retry(defer=CLAIM_RETRY_SECONDS)
    if claim_result != CLAIMED:
        return
    terminal_stored = False
    try:
        await metrics.record_started(job_id)
        operation = Operation(record["operation"])
        files = json.loads(record["files"])
        options = json.loads(record["options"])
        operation_result = await execute(operation, files, root / "output", options)
        result = operation_result.path
        result_bytes = result.stat().st_size
        stored = await store_terminal_state(
            redis,
            key,
            job_id,
            run_token,
            {
                "status": JobStatus.SUCCEEDED.value,
                "progress": record["total"],
                "result_path": str(result),
                "result_name": result.name,
                "result_type": result_mime(result),
                "warnings": json.dumps(
                    [
                        warning.model_dump(exclude_none=True)
                        for warning in operation_result.warnings
                    ],
                    ensure_ascii=False,
                ),
            },
        )
        terminal_stored = stored
        if not stored:
            return
        await metrics.record_terminal(
            job_id=job_id,
            status=JobStatus.SUCCEEDED,
            result_bytes=result_bytes,
        )
    except JobFailure as exc:
        stored = await store_terminal_state(
            redis,
            key,
            job_id,
            run_token,
            {
                "status": JobStatus.FAILED.value,
                "error_code": exc.code.value,
                "error_message": exc.message,
                "error_details": json.dumps(exc.details or {}),
            },
        )
        terminal_stored = stored
        if stored:
            await metrics.record_terminal(
                job_id=job_id, status=JobStatus.FAILED, error_code=exc.code.value
            )
    except Exception:
        logger.error(
            "job_failed operation=%s error_code=%s",
            record.get("operation", "unknown"),
            ErrorCode.INTERNAL_ERROR.value,
        )
        stored = await store_terminal_state(
            redis,
            key,
            job_id,
            run_token,
            {
                "status": JobStatus.FAILED.value,
                "error_code": ErrorCode.INTERNAL_ERROR.value,
                "error_message": "The job failed unexpectedly",
            },
        )
        terminal_stored = stored
        if stored:
            await metrics.record_terminal(
                job_id=job_id,
                status=JobStatus.FAILED,
                error_code=ErrorCode.INTERNAL_ERROR.value,
            )
    finally:
        finished_status = await finish_job_claim(redis, key, run_token)
        if terminal_stored or finished_status == JobStatus.CANCELLED.value:
            if finished_status == JobStatus.CANCELLED.value:
                delete_job_directory(job_id)
            else:
                input_dir = root / "input"
                if input_dir.exists():
                    for path in input_dir.iterdir():
                        path.unlink(missing_ok=True)
            await release_active_job(redis, ip_hash, job_id)
        await heartbeat(ctx)


class WorkerSettings:
    functions = [run_operation]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    queue_name = os.getenv("ARQ_QUEUE", "local")
    max_jobs = int(os.getenv("WORKER_CONCURRENCY", "1"))
    job_timeout = settings.job_timeout_seconds + 30
    keep_result = settings.job_ttl_seconds
    on_startup = on_startup
    on_shutdown = on_shutdown
