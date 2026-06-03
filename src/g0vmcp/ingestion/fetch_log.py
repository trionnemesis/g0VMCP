"""增量擷取記錄 — 已 SUCCESS 的 target 不重抓;BLOCKED 退避至 retry_after。

Why: 反爬倫理 + 半月批次重疊 — 不全量重爬已成功的案號,且被擋下時退避而非
重試風暴。語義對齊持久層 repository/schema.py 的 fetch_log(status/retry_after):
- SUCCESS → 不抓
- BLOCKED 且 retry_after 未到 → 不抓(退避中)
- 其餘(BLOCKED 已過期 / FAILED / PENDING / 未記錄)→ 抓

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
                http_status INTEGER,
                retry_after TEXT
            )
            """
        )
        self._conn.commit()

    def record(
        self,
        target: str,
        status: FetchStatus,
        http_status: Optional[int] = None,
        retry_after: Optional[datetime] = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO fetch_log (target, status, fetched_at, http_status, retry_after)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(target) DO UPDATE SET
                status = excluded.status,
                fetched_at = excluded.fetched_at,
                http_status = excluded.http_status,
                retry_after = excluded.retry_after
            """,
            (
                target,
                status.value,
                datetime.now(timezone.utc).isoformat(),
                http_status,
                retry_after.isoformat() if retry_after is not None else None,
            ),
        )
        self._conn.commit()

    def record_blocked(
        self,
        target: str,
        retry_after: datetime,
        http_status: Optional[int] = None,
    ) -> None:
        """被反爬擋下:標記 BLOCKED 並設退避截止時間 retry_after。"""
        self.record(
            target,
            FetchStatus.BLOCKED,
            http_status=http_status,
            retry_after=retry_after,
        )

    def status_of(self, target: str) -> Optional[FetchStatus]:
        row = self._conn.execute(
            "SELECT status FROM fetch_log WHERE target = ?", (target,)
        ).fetchone()
        return FetchStatus(row[0]) if row else None

    def should_fetch(self, target: str, now: Optional[datetime] = None) -> bool:
        """SUCCESS → False;BLOCKED 且 retry_after 未到 → False;其餘 → True。"""
        row = self._conn.execute(
            "SELECT status, retry_after FROM fetch_log WHERE target = ?",
            (target,),
        ).fetchone()
        if row is None:
            return True
        status, retry_after = row[0], row[1]
        if status == FetchStatus.SUCCESS.value:
            return False
        if status == FetchStatus.BLOCKED.value and retry_after is not None:
            now = now or datetime.now(timezone.utc)
            # retry_after 未到 → 仍在退避期,不抓
            return now >= datetime.fromisoformat(retry_after)
        return True

    def close(self) -> None:
        self._conn.close()
