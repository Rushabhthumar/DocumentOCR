from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.config import Settings, get_settings
from app.models.verification_models import VerificationResult


class HistoryService:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.db_path = self.settings.history_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS verification_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    status_updated INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_verification_txn
                ON verification_history(transaction_id, timestamp DESC)
                """
            )
            conn.commit()

    def save(self, result: VerificationResult) -> None:
        # Avoid storing full raw transaction unnecessarily; keep snapshot essentials
        payload = result.model_dump(mode="json")
        if payload.get("transaction") and isinstance(payload["transaction"], dict):
            payload["transaction"].pop("raw", None)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO verification_history
                (transaction_id, timestamp, decision, status_updated, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    result.transaction_id,
                    result.timestamp.isoformat(),
                    result.decision.value,
                    1 if result.status_updated else 0,
                    json.dumps(payload),
                ),
            )
            conn.commit()

    def latest(self, transaction_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json FROM verification_history
                WHERE transaction_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (transaction_id,),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["payload_json"])

    def history(self, transaction_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json FROM verification_history
                WHERE transaction_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (transaction_id, limit),
            ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]