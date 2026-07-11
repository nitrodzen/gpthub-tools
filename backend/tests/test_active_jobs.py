import time

import pytest

from app.storage import active_jobs_key, release_active_job, reserve_active_job


class FakeRedis:
    def __init__(self) -> None:
        self.strings: dict[str, tuple[str, int]] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    async def eval(self, _script: str, _keys: int, key: str, *args: object) -> int:
        if len(args) == 1:
            job_id = str(args[0])
            if key in self.strings:
                if self.strings[key][0] == job_id:
                    self.strings.pop(key)
                return 1
            self.zsets.get(key, {}).pop(job_id, None)
            if not self.zsets.get(key):
                self.zsets.pop(key, None)
            return 1
        now, limit, expires_at, job_id, _ttl = args
        now = float(now)
        limit = int(limit)
        expires_at = float(expires_at)
        job_id = str(job_id)
        if key in self.strings:
            legacy_job, remaining_ttl = self.strings.pop(key)
            self.zsets.setdefault(key, {})[legacy_job] = now + max(remaining_ttl, 1)
        jobs = self.zsets.setdefault(key, {})
        self.zsets[key] = {current: expiry for current, expiry in jobs.items() if expiry > now}
        if len(self.zsets[key]) >= limit:
            return 0
        self.zsets[key][job_id] = expires_at
        return 1

@pytest.mark.asyncio
async def test_three_jobs_are_allowed_and_the_slot_is_released() -> None:
    redis = FakeRedis()
    ip_hash = "test-ip"

    assert await reserve_active_job(redis, ip_hash, "job-1")
    assert await reserve_active_job(redis, ip_hash, "job-2")
    assert await reserve_active_job(redis, ip_hash, "job-3")
    assert not await reserve_active_job(redis, ip_hash, "job-4")

    await release_active_job(redis, ip_hash, "job-2")
    assert await reserve_active_job(redis, ip_hash, "job-4")


@pytest.mark.asyncio
async def test_legacy_single_active_job_key_is_migrated_safely() -> None:
    redis = FakeRedis()
    ip_hash = "legacy-ip"
    key = active_jobs_key(ip_hash)
    redis.strings[key] = ("legacy-job", 60)

    assert await reserve_active_job(redis, ip_hash, "new-job")
    assert "legacy-job" in redis.zsets[key]
    assert "new-job" in redis.zsets[key]

    redis.strings[key] = ("one-more-legacy-job", int(time.time()))
    await release_active_job(redis, ip_hash, "one-more-legacy-job")
    assert key not in redis.strings
