from __future__ import annotations

import asyncio
import time

from redis.asyncio import Redis

from .config import settings


async def cleanup_once(redis: Redis) -> int:
    settings.jobs_root.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - settings.result_ttl_seconds
    removed = 0
    for directory in settings.jobs_root.iterdir():
        if not directory.is_dir() or directory.stat().st_mtime >= cutoff:
            continue
        job_id = directory.name
        record = await redis.hgetall(f"job:{job_id}")
        if not record or record.get("status") in {"succeeded", "failed", "cancelled"}:
            import shutil

            shutil.rmtree(directory, ignore_errors=True)
            await redis.delete(f"job:{job_id}")
            removed += 1
    return removed


async def main() -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    settings.jobs_root.mkdir(parents=True, exist_ok=True)
    try:
        while True:
            await cleanup_once(redis)
            await asyncio.sleep(300)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
