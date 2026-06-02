#!/usr/bin/env python3
"""將超過 STALE_DAYS 無決標公告的 TENDERING 標案標記為 STALE。

背景：政府採購案若長期未決標、未更新，通常已流標或取消但未正式公告。
本腳本以最後公告日為基準，超過 180 天視為過期，lifecycle_state 標記 STALE。

使用方式: python3 scripts/mark_stale.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from g0vmcp.repository import build_repositories

DB_PATH = str(Path(__file__).resolve().parents[1] / "g0vmcp.db")
STALE_DAYS = 180


async def main(tender_repo) -> None:
    cutoff = (date.today() - timedelta(days=STALE_DAYS)).isoformat()

    # 找所有 TENDERING 且最後公告日早於 cutoff 的標案（無公告記錄也算過期）
    cur = await tender_repo._conn.execute(
        """
        SELECT t.tender_id, t.title, MAX(a.ann_date) AS latest_ann
        FROM tenders t
        LEFT JOIN announcements a ON a.tender_id = t.tender_id
        WHERE t.lifecycle_state = 'TENDERING'
        GROUP BY t.tender_id, t.title
        HAVING latest_ann IS NULL OR latest_ann < ?
        ORDER BY latest_ann ASC
        """,
        (cutoff,),
    )
    rows = await cur.fetchall()

    if not rows:
        print(f"無過期標案（截止基準：最後公告早於 {cutoff}）")
        return

    print(f"標記 STALE：{len(rows)} 筆（最後公告早於 {cutoff}）")
    for row in rows:
        await tender_repo._conn.execute(
            "UPDATE tenders SET lifecycle_state = 'STALE' WHERE tender_id = ?",
            (row[0],),
        )
        title_short = (row[1] or "")[:40]
        print(f"  STALE  {row[0]:<30}  latest_ann={row[2]}  {title_short}")

    await tender_repo._conn.commit()
    print("完成。")


if __name__ == "__main__":
    tender_repo, _ = build_repositories(DB_PATH)
    asyncio.run(main(tender_repo))
