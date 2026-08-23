import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import cleanup
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


class CleanupRedis:
    def __init__(
        self,
        record: dict[str, str],
        expired: list[str] | None = None,
        live_owners: set[str] | None = None,
    ) -> None:
        self.record = record
        self.expired = expired or []
        self.live_owners = live_owners or set()
        self.deleted: list[str] = []
        self.zremmed: list[tuple[str, str]] = []
        self.zadded: list[tuple[str, dict[str, float]]] = []

    async def hgetall(self, _key: str) -> dict[str, str]:
        return dict(self.record)

    async def zrangebyscore(self, *_args, **_kwargs) -> list[str]:
        return list(self.expired)

    async def delete(self, key: str) -> None:
        self.deleted.append(key)

    async def zrem(self, key: str, value: str) -> None:
        self.zremmed.append((key, value))

    async def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.zadded.append((key, mapping))

    async def exists(self, key: str) -> bool:
        return key.removeprefix("worker-heartbeat:") in self.live_owners


@pytest.mark.asyncio
async def test_orphan_scan_keeps_fresh_terminal_result_from_long_queued_job(
    tmp_path, monkeypatch
) -> None:
    job_root = tmp_path / "long-queue"
    job_root.mkdir()
    old = time.time() - 7200
    os.utime(job_root, (old, old))
    redis = CleanupRedis(
        {
            "status": "succeeded",
            "ip_hash": "ip",
            "expires_at": "2099-01-01T00:00:00Z",
        }
    )
    monkeypatch.setattr(
        cleanup,
        "settings",
        SimpleNamespace(jobs_root=tmp_path, result_ttl_seconds=3600),
    )

    assert await cleanup.cleanup_once(redis) == 0
    assert job_root.exists()
    assert redis.deleted == []


@pytest.mark.asyncio
async def test_expiration_sweep_does_not_delete_running_job(tmp_path, monkeypatch) -> None:
    job_root = tmp_path / "running-job"
    job_root.mkdir()
    redis = CleanupRedis(
        {
            "status": "running",
            "ip_hash": "ip",
            "run_token": "token",
            "run_owner": "owner",
        },
        ["running-job"],
        {"owner"},
    )
    release = AsyncMock()
    monkeypatch.setattr(cleanup, "settings", SimpleNamespace(jobs_root=tmp_path))
    monkeypatch.setattr(cleanup, "release_active_job", release)

    assert await cleanup.cleanup_expired(redis) == 0
    assert job_root.exists()
    assert redis.deleted == []
    assert redis.zremmed == []
    assert redis.zadded[0][0] == "job-expirations"
    release.assert_not_awaited()


@pytest.mark.asyncio
async def test_expiration_sweep_cleans_running_job_with_dead_owner(tmp_path, monkeypatch) -> None:
    job_root = tmp_path / "dead-job"
    job_root.mkdir()
    redis = CleanupRedis(
        {
            "status": "running",
            "ip_hash": "ip",
            "run_token": "token",
            "run_owner": "dead-owner",
        },
        ["dead-job"],
    )
    release = AsyncMock()
    monkeypatch.setattr(cleanup, "settings", SimpleNamespace(jobs_root=tmp_path))
    monkeypatch.setattr(cleanup, "release_active_job", release)

    assert await cleanup.cleanup_expired(redis) == 1
    assert not job_root.exists()
    assert redis.deleted == ["job:dead-job"]
    release.assert_awaited_once_with(redis, "ip", "dead-job")
