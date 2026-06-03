#!/usr/bin/env python3
"""階段二 enrich:對 baseline 暫定 IT(llm_fallback)候選補明細頁 CPC 碼 + 加值欄位。

取代 enrich_open_date.py(泛化:不只補 open_date,還以官方標的分類 CPC 碼精確確認
資訊服務類)。流程:
  查 method='llm_fallback' 候選 → readTenderBasic 反查 org_id → 明細頁 → CPC 碼
  → 45/84/47 確認 IT(official_code);非 IT 重分類(待 purge 剔除)+ 補預算/開標等。

被反爬擋下 → BLOCKED 退避 4h 並中止本批;fetch_log.retry_after 支撐漸進補完
(多輪執行直到 method 全轉 official_code)。

使用方式:
  python3 scripts/enrich_details.py                # 每批上限 30
  python3 scripts/enrich_details.py --batch-size 10
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from g0vmcp.ingestion.cf_http import CloudflareAwareHttpGetter
from g0vmcp.ingestion.fetch_log import FetchLog
from g0vmcp.ingestion.fetcher import PccHttpFetcher
from g0vmcp.ingestion.pipeline import IngestionPipeline
from g0vmcp.repository import build_repositories

_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = str(_ROOT / "g0vmcp.db")
FETCH_LOG_PATH = str(_ROOT / "fetch_log.db")


async def main(tender_repo, args) -> None:
    fetcher = PccHttpFetcher(CloudflareAwareHttpGetter())
    fetch_log = FetchLog(FETCH_LOG_PATH)
    pipe = IngestionPipeline(repo=tender_repo, fetch_log=fetch_log, fetcher=fetcher)

    print(f"=== enrich(每批上限 {args.batch_size})===")
    stats = await pipe.enrich(batch_size=args.batch_size)
    print(
        f"確認 IT {stats.confirmed_it} / 重分類非IT {stats.reclassified} / "
        f"無CPC碼 {stats.no_cpc} / 封鎖 {stats.blocked} / 失敗 {stats.failed}"
    )
    if stats.blocked:
        print("⛔ 遭速率封鎖,已退避 4h。稍後再執行可從中斷處續補。")
    elif stats.confirmed_it or stats.reclassified or stats.no_cpc:
        print("本批完成。若仍有 llm_fallback 候選,再執行一次續補。")
    else:
        print("無待補候選(全部已確認分類)。")
    fetch_log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="明細頁 CPC 碼 enrich")
    parser.add_argument("--batch-size", type=int, default=30, help="每批爬取上限")
    args = parser.parse_args()

    tender_repo, _ = build_repositories(DB_PATH)
    asyncio.run(main(tender_repo, args))
