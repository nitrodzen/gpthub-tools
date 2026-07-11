from __future__ import annotations

import asyncio
import shutil
import time

from redis.asyncio import Redis

from .config import settings
from .storage import release_active_job


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
            shutil.rmtree(directory, ignore_errors=True)
            if record and record.get("ip_hash"):
                await release_active_job(redis, record["ip_hash"], job_id)
            await redis.delete(f"job:{job_id}")
            await redis.zrem("job-expirations", job_id)
            removed += 1
    return removed


async def cleanup_expired(redis: Redis) -> int:
    expired = await redis.zrangebyscore("job-expirations", min=0, max=time.time(), start=0, num=100)
    removed = 0
    for raw_job_id in expired:
        job_id = raw_job_id.decode() if isinstance(raw_job_id, bytes) else raw_job_id
        record = await redis.hgetall(f"job:{job_id}")
        if record.get("ip_hash"):
            await release_active_job(redis, record["ip_hash"], job_id)
        shutil.rmtree(settings.jobs_root / job_id, ignore_errors=True)
        await redis.delete(f"job:{job_id}")
        await redis.zrem("job-expirations", job_id)
        removed += 1
    return removed


async def main() -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    settings.jobs_root.mkdir(parents=True, exist_ok=True)
    next_orphan_scan = 0.0
    try:
        while True:
            await cleanup_expired(redis)
            now = time.monotonic()
            if now >= next_orphan_scan:
                await cleanup_once(redis)
                next_orphan_scan = now + 300
            await asyncio.sleep(1)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
