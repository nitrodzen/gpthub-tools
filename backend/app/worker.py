from __future__ import annotations

import json
import os
import socket
import traceback

from arq import cron
from arq.connections import RedisSettings

from .config import settings
from .models import ErrorCode, JobFailure, JobStatus, Operation
from .operations import execute, result_mime
from .storage import delete_job_directory

WORKER_ID = f"{os.getenv('ARQ_QUEUE', 'local')}:{socket.gethostname()}:{os.getpid()}"


def decoded(value: bytes | str | None) -> str | None:
    return value.decode() if isinstance(value, bytes) else value


async def store_terminal_state(redis, key: str, mapping: dict[str, str]) -> bool:
    arguments: list[str] = []
    for field, value in mapping.items():
        arguments.extend((field, value))
    changed = await redis.eval(
        """
        if redis.call('HGET', KEYS[1], 'status') == 'cancelled' then
            return 0
        end
        redis.call('HSET', KEYS[1], unpack(ARGV))
        return 1
        """,
        1,
        key,
        *arguments,
    )
    return bool(changed)


async def heartbeat(ctx: dict) -> None:
    await ctx["redis"].set(f"worker-heartbeat:{WORKER_ID}", "1", ex=90)


async def on_startup(ctx: dict) -> None:
    await heartbeat(ctx)


async def on_shutdown(ctx: dict) -> None:
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
        if record:
            delete_job_directory(job_id)
            await redis.delete(f"active:{record['ip_hash']}")
        return
    ip_hash = record["ip_hash"]
    root = settings.jobs_root / job_id
    try:
        await redis.hset(key, mapping={"status": JobStatus.RUNNING.value, "progress": "0"})
        operation = Operation(record["operation"])
        files = json.loads(record["files"])
        options = json.loads(record["options"])
        result = await execute(operation, files, root / "output", options)
        stored = await store_terminal_state(
            redis,
            key,
            {
                "status": JobStatus.SUCCEEDED.value,
                "progress": record["total"],
                "result_path": str(result),
                "result_name": result.name,
                "result_type": result_mime(result),
            },
        )
        if not stored:
            delete_job_directory(job_id)
            return
    except JobFailure as exc:
        stored = await store_terminal_state(
            redis,
            key,
            {
                "status": JobStatus.FAILED.value,
                "error_code": exc.code.value,
                "error_message": exc.message,
                "error_details": json.dumps(exc.details or {}),
            },
        )
        if not stored:
            delete_job_directory(job_id)
    except Exception:
        traceback.print_exc()
        stored = await store_terminal_state(
            redis,
            key,
            {
                "status": JobStatus.FAILED.value,
                "error_code": ErrorCode.INTERNAL_ERROR.value,
                "error_message": "The job failed unexpectedly",
            },
        )
        if not stored:
            delete_job_directory(job_id)
    finally:
        input_dir = root / "input"
        if input_dir.exists():
            for path in input_dir.iterdir():
                path.unlink(missing_ok=True)
        await redis.delete(f"active:{ip_hash}")
        await heartbeat(ctx)


class WorkerSettings:
    functions = [run_operation]
    cron_jobs = [cron(heartbeat, second={0, 30})]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    queue_name = os.getenv("ARQ_QUEUE", "local")
    max_jobs = int(os.getenv("WORKER_CONCURRENCY", "1"))
    job_timeout = settings.job_timeout_seconds
    keep_result = settings.job_ttl_seconds
    on_startup = on_startup
    on_shutdown = on_shutdown
