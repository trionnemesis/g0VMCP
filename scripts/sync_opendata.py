#!/usr/bin/env python3
"""階段一 baseline:動態下載 web.pcc 半月公開資料 XML → g0vmcp.db。

取代硬編碼的 ingest_from_pcc_tender.py。流程:
  下載 tender_*.xml(招標,近期進行中) + award_*.xml(決標,近 2 年)
  → parse → scope 過濾(衛福部 ∩ 關鍵字初篩,黑名單剔除)
  → 暫定 Category(IT, llm_fallback)落庫 → fetch_log PENDING。

CPC 碼精確分類 + 加值欄位由 enrich_details.py(階段二)補完。
半月檔走 downloadFile?fileName= 路由,無 Cloudflare gate(spike 已驗證)。

使用方式:
  python3 scripts/sync_opendata.py                 # 招標近 3 月 + 決標近 24 月
  python3 scripts/sync_opendata.py --award-months 2  # 縮小決標範圍(測試/首次)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from g0vmcp.ingestion.cf_http import CloudflareAwareHttpGetter
from g0vmcp.ingestion.fetch_log import FetchLog
from g0vmcp.ingestion.opendata import (
    award_xml_url,
    parse_award_xml,
    parse_tender_xml,
    tender_xml_url,
)
from g0vmcp.ingestion.pipeline import IngestionPipeline
from g0vmcp.repository import build_repositories

_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = str(_ROOT / "g0vmcp.db")
FETCH_LOG_PATH = str(_ROOT / "fetch_log.db")  # 獨立檔,避開 schema.py 同名表


def _halfmonth_periods(months_back: int, today: date) -> list[tuple[int, int, int]]:
    """從 today 往回 months_back 個月,每月含上/下半月。"""
    periods: list[tuple[int, int, int]] = []
    y, m = today.year, today.month
    for _ in range(months_back):
        periods.append((y, m, 1))
        periods.append((y, m, 2))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return periods


async def _fetch_xml(getter: CloudflareAwareHttpGetter, url: str) -> str | None:
    """下載半月 XML;檔案不存在 / 非 XML 回 None(跳過該期)。"""
    try:
        resp = await getter(url)
    except Exception as exc:  # noqa: BLE001 — 單期失敗不中斷
        print(f"  ✗ {url.rsplit('=', 1)[-1]}: {type(exc).__name__}")
        return None
    text = resp.text.lstrip()
    if resp.status_code == 200 and text.startswith("<?xml"):
        return resp.text
    return None


async def main(tender_repo, args) -> None:
    getter = CloudflareAwareHttpGetter()
    fetch_log = FetchLog(FETCH_LOG_PATH)
    pipe = IngestionPipeline(repo=tender_repo, fetch_log=fetch_log)
    today = date.today()

    # --- 招標(進行中) ---
    print(f"=== 招標 baseline(近 {args.tender_months} 月) ===")
    t_saved = t_skip_org = t_skip_bl = 0
    for y, m, h in _halfmonth_periods(args.tender_months, today):
        xml = await _fetch_xml(getter, tender_xml_url(y, m, h))
        if xml is None:
            continue
        rows = parse_tender_xml(xml)
        stats = await pipe.ingest_tender_rows(rows)
        t_saved += stats.saved
        t_skip_org += stats.skipped_non_mohw
        t_skip_bl += stats.skipped_blacklist
        print(f"  {y}/{m:02d}-{h}: {len(rows):>5} 筆 → 衛福部IT候選 {stats.saved}")
    print(f"招標落庫 {t_saved}(非衛福部略過 {t_skip_org}、黑名單略過 {t_skip_bl})")

    # --- 決標(近 2 年) ---
    print(f"\n=== 決標 baseline(近 {args.award_months} 月) ===")
    a_saved = a_skip_org = a_skip_bl = 0
    for y, m, h in _halfmonth_periods(args.award_months, today):
        xml = await _fetch_xml(getter, award_xml_url(y, m, h))
        if xml is None:
            continue
        rows = parse_award_xml(xml)
        stats = await pipe.ingest_award_rows(rows)
        a_saved += stats.saved
        a_skip_org += stats.skipped_non_mohw
        a_skip_bl += stats.skipped_blacklist
        print(f"  {y}/{m:02d}-{h}: {len(rows):>5} 筆 → 衛福部IT候選 {stats.saved}")
    print(f"決標落庫 {a_saved}(非衛福部略過 {a_skip_org}、黑名單略過 {a_skip_bl})")

    fetch_log.close()
    print(f"\nbaseline 完成。下一步:python3 scripts/enrich_details.py 補 CPC 碼。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="web.pcc 半月 XML baseline 同步")
    parser.add_argument("--tender-months", type=int, default=3, help="招標回溯月數")
    parser.add_argument("--award-months", type=int, default=24, help="決標回溯月數")
    args = parser.parse_args()

    tender_repo, _ = build_repositories(DB_PATH)
    asyncio.run(main(tender_repo, args))
