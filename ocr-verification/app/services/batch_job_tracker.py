from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import Settings, get_settings


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BatchJobTracker:
    """Tracks RabbitMQ batch jobs and per-transaction outcomes."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._lock = threading.Lock()
        self._path = Path(self.settings.temp_path) / "batch_jobs.db"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self._path), check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._lock:
            con = self._connect()
            try:
                con.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS batch_jobs (
                        job_id TEXT PRIMARY KEY,
                        company_id TEXT,
                        period_id INTEGER,
                        status TEXT,
                        total_queued INTEGER DEFAULT 0,
                        completed INTEGER DEFAULT 0,
                        failed INTEGER DEFAULT 0,
                        skipped INTEGER DEFAULT 0,
                        message TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS batch_job_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT,
                        transaction_id TEXT,
                        status TEXT,
                        decision TEXT,
                        message TEXT,
                        errors_json TEXT,
                        updated_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_batch_job_items_job
                        ON batch_job_items(job_id);
                    """
                )
                con.commit()
            finally:
                con.close()

    def create_job(
        self,
        *,
        company_id: str,
        period_id: int,
        total_queued: int,
        skipped: int = 0,
        message: str = "",
    ) -> str:
        job_id = str(uuid.uuid4())
        now = _utc_now()
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    """
                    INSERT INTO batch_jobs (
                        job_id, company_id, period_id, status, total_queued,
                        completed, failed, skipped, message, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        company_id,
                        period_id,
                        "QUEUED",
                        total_queued,
                        skipped,
                        message,
                        now,
                        now,
                    ),
                )
                con.commit()
            finally:
                con.close()
        return job_id

    def mark_running(self, job_id: str) -> None:
        self._update_job(job_id, status="RUNNING")

    def record_item(
        self,
        *,
        job_id: str,
        transaction_id: str,
        status: str,
        decision: Optional[str] = None,
        message: str = "",
        errors: Optional[list[str]] = None,
    ) -> None:
        now = _utc_now()
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    """
                    INSERT INTO batch_job_items (
                        job_id, transaction_id, status, decision, message, errors_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        transaction_id,
                        status,
                        decision,
                        message,
                        json.dumps(errors or []),
                        now,
                    ),
                )
                if status == "COMPLETED":
                    con.execute(
                        "UPDATE batch_jobs SET completed = completed + 1, updated_at = ? WHERE job_id = ?",
                        (now, job_id),
                    )
                elif status == "FAILED":
                    con.execute(
                        "UPDATE batch_jobs SET failed = failed + 1, updated_at = ? WHERE job_id = ?",
                        (now, job_id),
                    )
                elif status == "SKIPPED":
                    con.execute(
                        "UPDATE batch_jobs SET skipped = skipped + 1, updated_at = ? WHERE job_id = ?",
                        (now, job_id),
                    )
                row = con.execute(
                    "SELECT total_queued, completed, failed, skipped FROM batch_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if row:
                    done = int(row["completed"]) + int(row["failed"])
                    # skipped at create already counted; items still in flight = total_queued - done
                    if done >= int(row["total_queued"]):
                        con.execute(
                            """
                            UPDATE batch_jobs
                            SET status = 'COMPLETED', updated_at = ?,
                                message = ?
                            WHERE job_id = ?
                            """,
                            (
                                now,
                                f"Batch complete. queued={row['total_queued']}, "
                                f"completed={row['completed']}, failed={row['failed']}, "
                                f"skipped={row['skipped']}",
                                job_id,
                            ),
                        )
                    else:
                        con.execute(
                            "UPDATE batch_jobs SET status = 'RUNNING', updated_at = ? WHERE job_id = ?",
                            (now, job_id),
                        )
                con.commit()
            finally:
                con.close()

    def _update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = _utc_now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [job_id]
        with self._lock:
            con = self._connect()
            try:
                con.execute(f"UPDATE batch_jobs SET {cols} WHERE job_id = ?", vals)
                con.commit()
            finally:
                con.close()

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT * FROM batch_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                if not row:
                    return None
                items = con.execute(
                    """
                    SELECT transaction_id, status, decision, message, errors_json, updated_at
                    FROM batch_job_items WHERE job_id = ? ORDER BY id
                    """,
                    (job_id,),
                ).fetchall()
                result = dict(row)
                result["pending"] = max(
                    0,
                    int(result["total_queued"])
                    - int(result["completed"])
                    - int(result["failed"]),
                )
                result["items"] = [
                    {
                        "transaction_id": i["transaction_id"],
                        "status": i["status"],
                        "decision": i["decision"],
                        "message": i["message"],
                        "errors": json.loads(i["errors_json"] or "[]"),
                        "updated_at": i["updated_at"],
                    }
                    for i in items
                ]
                return result
            finally:
                con.close()

    def active_item_count(self) -> int:
        """Jobs still QUEUED/RUNNING — used for 50-min renew gate."""
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    """
                    SELECT COALESCE(SUM(
                        CASE
                          WHEN total_queued - completed - failed > 0
                          THEN total_queued - completed - failed
                          ELSE 0
                        END
                    ), 0) AS pending
                    FROM batch_jobs
                    WHERE status IN ('QUEUED', 'RUNNING')
                    """
                ).fetchone()
                return int(row["pending"] if row else 0)
            finally:
                con.close()


batch_job_tracker = BatchJobTracker()
