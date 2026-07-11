from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import sqlite3
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import settings
from .models import JobStatus, Operation

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {
    JobStatus.SUCCEEDED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
}


class MetricsUnavailable(RuntimeError):
    """The optional metrics store cannot be read at the moment."""


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")


def _job_digest(job_id: str) -> str:
    return hmac.new(
        settings.app_secret.encode(), job_id.encode(), hashlib.sha256
    ).hexdigest()


def _percentiles(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "average": None, "p50": None, "p95": None}
    ordered = sorted(values)

    def percentile(value: float) -> float:
        index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * value)))
        return round(ordered[index], 3)

    return {
        "count": len(ordered),
        "average": round(sum(ordered) / len(ordered), 3),
        "p50": percentile(0.5),
        "p95": percentile(0.95),
    }


def _empty_summary() -> dict[str, Any]:
    return {
        "jobs": {
            "accepted": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
            "rejected": 0,
        },
        "filesAccepted": 0,
        "inputBytes": 0,
        "resultBytes": 0,
        "_queueDurations": [],
        "_processingDurations": [],
    }


def _public_summary(summary: dict[str, Any], *, timing: bool = True) -> dict[str, Any]:
    result = {
        "jobs": summary["jobs"],
        "filesAccepted": summary["filesAccepted"],
        "inputBytes": summary["inputBytes"],
        "resultBytes": summary["resultBytes"],
    }
    if timing:
        result["timing"] = {
            "queueSeconds": _percentiles(summary["_queueDurations"]),
            "processingSeconds": _percentiles(summary["_processingDurations"]),
        }
    return result


class MetricsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.metrics_db_path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS job_metrics (
                job_digest TEXT PRIMARY KEY,
                operation TEXT NOT NULL,
                accepted_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL,
                status TEXT NOT NULL,
                file_count INTEGER NOT NULL,
                input_bytes INTEGER NOT NULL,
                result_bytes INTEGER NOT NULL DEFAULT 0,
                error_code TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS metric_rejections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at REAL NOT NULL,
                operation TEXT NOT NULL,
                file_count INTEGER NOT NULL,
                input_bytes INTEGER NOT NULL,
                error_code TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS job_metrics_accepted_at ON job_metrics(accepted_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS job_metrics_finished_at ON job_metrics(finished_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS metric_rejections_occurred_at "
            "ON metric_rejections(occurred_at)"
        )
        return connection

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        with self._connect():
            pass

    async def record_accepted(
        self,
        *,
        job_id: str,
        operation: Operation,
        file_count: int,
        input_bytes: int,
        accepted_at: float | None = None,
    ) -> None:
        await self._safe_write(
            self._record_accepted,
            job_id,
            operation.value,
            file_count,
            input_bytes,
            accepted_at or time.time(),
        )

    def _record_accepted(
        self,
        job_id: str,
        operation: str,
        file_count: int,
        input_bytes: int,
        accepted_at: float,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO job_metrics
                    (job_digest, operation, accepted_at, status, file_count, input_bytes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _job_digest(job_id),
                    operation,
                    accepted_at,
                    JobStatus.QUEUED.value,
                    file_count,
                    input_bytes,
                ),
            )

    async def record_rejected(
        self,
        *,
        operation: Operation,
        file_count: int,
        input_bytes: int,
        error_code: str,
        occurred_at: float | None = None,
    ) -> None:
        await self._safe_write(
            self._record_rejected,
            operation.value,
            file_count,
            input_bytes,
            error_code,
            occurred_at or time.time(),
        )

    def _record_rejected(
        self,
        operation: str,
        file_count: int,
        input_bytes: int,
        error_code: str,
        occurred_at: float,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO metric_rejections
                    (occurred_at, operation, file_count, input_bytes, error_code)
                VALUES (?, ?, ?, ?, ?)
                """,
                (occurred_at, operation, file_count, input_bytes, error_code),
            )

    async def record_started(self, job_id: str, started_at: float | None = None) -> None:
        await self._safe_write(self._record_started, job_id, started_at or time.time())

    def _record_started(self, job_id: str, started_at: float) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE job_metrics
                SET started_at = COALESCE(started_at, ?), status = ?
                WHERE job_digest = ? AND status = ?
                """,
                (started_at, JobStatus.RUNNING.value, _job_digest(job_id), JobStatus.QUEUED.value),
            )

    async def record_terminal(
        self,
        *,
        job_id: str,
        status: JobStatus,
        error_code: str | None = None,
        result_bytes: int = 0,
        finished_at: float | None = None,
    ) -> None:
        if status.value not in TERMINAL_STATUSES:
            raise ValueError("Only terminal job states can be recorded as metrics")
        await self._safe_write(
            self._record_terminal,
            job_id,
            status.value,
            error_code,
            result_bytes,
            finished_at or time.time(),
        )

    def _record_terminal(
        self,
        job_id: str,
        status: str,
        error_code: str | None,
        result_bytes: int,
        finished_at: float,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE job_metrics
                SET status = ?, finished_at = ?, error_code = ?, result_bytes = ?
                WHERE job_digest = ? AND status IN (?, ?)
                """,
                (
                    status,
                    finished_at,
                    error_code,
                    result_bytes,
                    _job_digest(job_id),
                    JobStatus.QUEUED.value,
                    JobStatus.RUNNING.value,
                ),
            )

    async def prune(self, older_than: float) -> None:
        await self._safe_write(self._prune, older_than)

    def _prune(self, older_than: float) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM job_metrics WHERE accepted_at < ?", (older_than,))
            connection.execute("DELETE FROM metric_rejections WHERE occurred_at < ?", (older_than,))
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")

    async def report(self, *, from_date: date, to_date: date, bucket: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._report, from_date, to_date, bucket)

    def _report(self, from_date: date, to_date: date, bucket: str) -> dict[str, Any]:
        from_timestamp = datetime.combine(from_date, datetime.min.time(), UTC).timestamp()
        to_timestamp = datetime.combine(
            to_date + timedelta(days=1), datetime.min.time(), UTC
        ).timestamp()
        with self._connect() as connection:
            jobs = connection.execute(
                """
                SELECT operation, accepted_at, started_at, finished_at, status,
                       file_count, input_bytes, result_bytes, error_code
                FROM job_metrics
                WHERE (accepted_at >= ? AND accepted_at < ?)
                   OR (finished_at IS NOT NULL AND finished_at >= ? AND finished_at < ?)
                """,
                (from_timestamp, to_timestamp, from_timestamp, to_timestamp),
            ).fetchall()
            rejections = connection.execute(
                """
                SELECT operation, occurred_at, file_count, input_bytes, error_code
                FROM metric_rejections
                WHERE occurred_at >= ? AND occurred_at < ?
                """,
                (from_timestamp, to_timestamp),
            ).fetchall()

        series = self._empty_series(from_timestamp, to_timestamp, bucket)
        summary = _empty_summary()
        operations: dict[str, dict[str, Any]] = {}
        errors: Counter[str] = Counter()

        def operation_summary(operation: str) -> dict[str, Any]:
            return operations.setdefault(operation, _empty_summary())

        def add_accepted(target: dict[str, Any], row: sqlite3.Row) -> None:
            target["jobs"]["accepted"] += 1
            target["filesAccepted"] += int(row["file_count"])
            target["inputBytes"] += int(row["input_bytes"])

        def add_terminal(target: dict[str, Any], row: sqlite3.Row) -> None:
            status = str(row["status"])
            if status in target["jobs"]:
                target["jobs"][status] += 1
            target["resultBytes"] += int(row["result_bytes"] or 0)
            if row["started_at"] is not None:
                queue_duration = max(
                    0.0, float(row["started_at"]) - float(row["accepted_at"])
                )
                processing_duration = max(
                    0.0, float(row["finished_at"]) - float(row["started_at"])
                )
                target["_queueDurations"].append(queue_duration)
                target["_processingDurations"].append(processing_duration)

        for row in jobs:
            operation = str(row["operation"])
            if from_timestamp <= float(row["accepted_at"]) < to_timestamp:
                add_accepted(summary, row)
                add_accepted(operation_summary(operation), row)
                accepted_bucket = series[self._bucket_timestamp(float(row["accepted_at"]), bucket)]
                add_accepted(accepted_bucket, row)
                operation_series = accepted_bucket["operations"].setdefault(
                    operation, _empty_summary()
                )
                add_accepted(operation_series, row)
            if (
                row["finished_at"] is not None
                and from_timestamp <= float(row["finished_at"]) < to_timestamp
            ):
                add_terminal(summary, row)
                add_terminal(operation_summary(operation), row)
                bucket_summary = series[self._bucket_timestamp(float(row["finished_at"]), bucket)]
                add_terminal(bucket_summary, row)
                operation_series = bucket_summary["operations"].setdefault(
                    operation, _empty_summary()
                )
                add_terminal(operation_series, row)
                if row["error_code"]:
                    errors[str(row["error_code"])] += 1

        for row in rejections:
            operation = str(row["operation"])
            summary["jobs"]["rejected"] += 1
            operation_summary(operation)["jobs"]["rejected"] += 1
            bucket_summary = series[self._bucket_timestamp(float(row["occurred_at"]), bucket)]
            bucket_summary["jobs"]["rejected"] += 1
            operation_summary_for_bucket = bucket_summary["operations"].setdefault(
                operation, _empty_summary()
            )
            operation_summary_for_bucket["jobs"]["rejected"] += 1
            errors[str(row["error_code"])] += 1

        return {
            "generatedAt": _iso(time.time()),
            "period": {
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "bucket": bucket,
                "timezone": "UTC",
            },
            "summary": _public_summary(summary),
            "operations": [
                {"operation": operation, **_public_summary(value)}
                for operation, value in sorted(operations.items())
            ],
            "errors": [
                {"code": code, "count": count} for code, count in errors.most_common()
            ],
            "series": [
                {
                    "start": _iso(timestamp),
                    **_public_summary(value, timing=False),
                    "operations": {
                        operation: _public_summary(operation_value, timing=False)
                        for operation, operation_value in sorted(value["operations"].items())
                    },
                }
                for timestamp, value in sorted(series.items())
            ],
        }

    def _empty_series(
        self, from_timestamp: float, to_timestamp: float, bucket: str
    ) -> dict[float, dict[str, Any]]:
        first = self._bucket_timestamp(from_timestamp, bucket)
        step = 3600 if bucket == "hour" else 86400
        result: dict[float, dict[str, Any]] = {}
        timestamp = first
        while timestamp < to_timestamp:
            summary = _empty_summary()
            summary["operations"] = {}
            result[timestamp] = summary
            timestamp += step
        return result

    @staticmethod
    def _bucket_timestamp(timestamp: float, bucket: str) -> float:
        value = datetime.fromtimestamp(timestamp, UTC)
        if bucket == "hour":
            value = value.replace(minute=0, second=0, microsecond=0)
        else:
            value = value.replace(hour=0, minute=0, second=0, microsecond=0)
        return value.timestamp()

    async def _safe_write(self, callback, *args: Any) -> None:
        try:
            await asyncio.to_thread(callback, *args)
        except (OSError, sqlite3.Error) as exc:
            logger.warning("Metrics write skipped: %s", exc)


metrics = MetricsStore()
