"""增量擷取記錄 — 已 SUCCESS 的 target 不重抓。

Why: 反爬倫理 + 半月批次重疊 — 不全量重爬已成功的案號。
ingestion/ 內自管,用 stdlib sqlite3(預設 in-memory)。對應 erm.dbml fetch_log。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from g0vmcp.contracts import FetchStatus


def detail_target(job_number: str, org_id: str) -> str:
    """target 命名:detail:{org_id}:{caseNo}(對照 erm.dbml fetch_log.target)。"""
    return f"detail:{org_id}:{job_number}"


class FetchLog:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fetch_log (
                target      TEXT PRIMARY KEY,
                status      TEXT NOT NULL,
                fetched_at  TEXT NOT NULL,
                http_status INTEGER
            )
            """
        )
        self._conn.commit()

    def record(
        self,
        target: str,
        status: FetchStatus,
        http_status: Optional[int] = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO fetch_log (target, status, fetched_at, http_status)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(target) DO UPDATE SET
                status = excluded.status,
                fetched_at = excluded.fetched_at,
                http_status = excluded.http_status
            """,
            (
                target,
                status.value,
                datetime.now(timezone.utc).isoformat(),
                http_status,
            ),
        )
        self._conn.commit()

    def status_of(self, target: str) -> Optional[FetchStatus]:
        row = self._conn.execute(
            "SELECT status FROM fetch_log WHERE target = ?", (target,)
        ).fetchone()
        return FetchStatus(row[0]) if row else None

    def should_fetch(self, target: str) -> bool:
        """已 SUCCESS → False(跳過);其餘狀態(含未記錄)→ True。"""
        return self.status_of(target) != FetchStatus.SUCCESS

    def close(self) -> None:
        self._conn.close()
