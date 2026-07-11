from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app import main
from app.metrics import MetricsStore
from app.models import JobStatus, Operation
from app.storage import token_digest


@pytest.mark.asyncio
async def test_metrics_report_tracks_lifecycle_without_job_identity(tmp_path) -> None:
    store = MetricsStore(tmp_path / "metrics.sqlite3")
    accepted_at = datetime(2026, 7, 11, 10, 0, tzinfo=UTC).timestamp()
    finished_at = datetime(2026, 7, 11, 10, 20, tzinfo=UTC).timestamp()

    await store.record_accepted(
        job_id="sensitive-job-id",
        operation=Operation.UPSCALE,
        file_count=2,
        input_bytes=1200,
        accepted_at=accepted_at,
    )
    await store.record_started("sensitive-job-id", accepted_at + 120)
    await store.record_terminal(
        job_id="sensitive-job-id",
        status=JobStatus.SUCCEEDED,
        result_bytes=2400,
        finished_at=finished_at,
    )
    # A duplicate terminal update must not turn a successful job into a cancellation.
    await store.record_terminal(
        job_id="sensitive-job-id",
        status=JobStatus.CANCELLED,
        finished_at=finished_at + 10,
    )
    await store.record_rejected(
        operation=Operation.PDF_MERGE,
        file_count=1,
        input_bytes=0,
        error_code="INVALID_FILE",
        occurred_at=accepted_at + 10,
    )

    report = await store.report(
        from_date=date(2026, 7, 11), to_date=date(2026, 7, 11), bucket="hour"
    )

    assert report["summary"]["jobs"] == {
        "accepted": 1,
        "succeeded": 1,
        "failed": 0,
        "cancelled": 0,
        "rejected": 1,
    }
    assert report["summary"]["filesAccepted"] == 2
    assert report["summary"]["inputBytes"] == 1200
    assert report["summary"]["resultBytes"] == 2400
    assert report["summary"]["timing"]["queueSeconds"]["p50"] == 120
    assert report["summary"]["timing"]["processingSeconds"]["p95"] == 1080
    assert report["errors"] == [{"code": "INVALID_FILE", "count": 1}]
    assert any(item["operation"] == "upscale" for item in report["operations"])

    with sqlite3.connect(store.path) as connection:
        stored_value = connection.execute("SELECT job_digest FROM job_metrics").fetchone()[0]
    assert stored_value != "sensitive-job-id"


@pytest.mark.asyncio
async def test_metrics_prune_and_write_failure_do_not_raise(tmp_path) -> None:
    store = MetricsStore(tmp_path / "metrics.sqlite3")
    await store.record_rejected(
        operation=Operation.IMAGE_CONVERT,
        file_count=1,
        input_bytes=0,
        error_code="INVALID_FILE",
        occurred_at=1,
    )
    await store.prune(2)
    report = await store.report(from_date=date(1970, 1, 1), to_date=date(1970, 1, 1), bucket="day")
    assert report["summary"]["jobs"]["rejected"] == 0

    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("blocked")
    unavailable = MetricsStore(blocked_parent / "metrics.sqlite3")
    await unavailable.record_rejected(
        operation=Operation.IMAGE_CONVERT,
        file_count=1,
        input_bytes=0,
        error_code="INVALID_FILE",
    )


class FakeJobRedis:
    def __init__(self, record: dict[str, str]) -> None:
        self.record = record
        self.deleted: list[str] = []
        self.zrem_calls: list[tuple[str, str]] = []
        self.hset_calls: list[dict[str, str]] = []

    async def hgetall(self, _key: str) -> dict[str, str]:
        return self.record

    async def delete(self, key: str) -> None:
        self.deleted.append(key)

    async def zrem(self, key: str, job_id: str) -> None:
        self.zrem_calls.append((key, job_id))

    async def hset(self, _key: str, mapping: dict[str, str]) -> None:
        self.hset_calls.append(mapping)


@pytest.mark.asyncio
async def test_delete_completed_job_keeps_metrics_but_removes_result(monkeypatch) -> None:
    token = "capability-token-123456789"
    redis = FakeJobRedis(
        {
            "job_id": "job-done",
            "token_hash": token_digest(token),
            "status": JobStatus.SUCCEEDED.value,
            "ip_hash": "hashed-ip",
        }
    )
    removed: list[str] = []
    monkeypatch.setattr(main, "delete_job_directory", removed.append)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))

    await main.delete_job(request, "job-done", token)

    assert removed == ["job-done"]
    assert redis.deleted == ["job:job-done"]
    assert redis.zrem_calls == [("job-expirations", "job-done")]
    assert redis.hset_calls == []
