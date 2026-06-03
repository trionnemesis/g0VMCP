"""CLI entry points for data management (sync / enrich / purge).

Installed as `g0vmcp-sync`, `g0vmcp-enrich`, `g0vmcp-purge`.
DB path resolution: --db flag > G0VMCP_DB env > ~/.g0vmcp/g0vmcp.db
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional


def _resolve_db(override: Optional[str] = None) -> str:
    if override:
        return override
    env = os.environ.get("G0VMCP_DB")
    if env:
        return env
    data_dir = Path.home() / ".g0vmcp"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "g0vmcp.db")


def _resolve_fetch_log(db_path: str) -> str:
    return str(Path(db_path).parent / "fetch_log.db")


# ── sync ─────────────────────────────────────────────────────────────────

def _halfmonth_periods(months_back: int, today: date) -> list[tuple[int, int, int]]:
    periods: list[tuple[int, int, int]] = []
    y, m = today.year, today.month
    for _ in range(months_back):
        periods.append((y, m, 1))
        periods.append((y, m, 2))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return periods


async def _sync(tender_repo, db_path: str, args: argparse.Namespace) -> None:
    from g0vmcp.ingestion.cf_http import CloudflareAwareHttpGetter
    from g0vmcp.ingestion.fetch_log import FetchLog
    from g0vmcp.ingestion.opendata import (
        award_xml_url,
        parse_award_xml,
        parse_tender_xml,
        tender_xml_url,
    )
    from g0vmcp.ingestion.pipeline import IngestionPipeline

    getter = CloudflareAwareHttpGetter()
    fetch_log = FetchLog(_resolve_fetch_log(db_path))
    pipe = IngestionPipeline(repo=tender_repo, fetch_log=fetch_log)
    today = date.today()

    async def fetch_xml(url: str) -> str | None:
        try:
            resp = await getter(url)
        except Exception:
            return None
        text = resp.text.lstrip()
        if resp.status_code == 200 and text.startswith("<?xml"):
            return resp.text
        return None

    print(f"DB: {db_path}")
    print(f"=== 招標 baseline（近 {args.tender_months} 月）===")
    t_saved = t_skip_org = t_skip_bl = 0
    for y, m, h in _halfmonth_periods(args.tender_months, today):
        xml = await fetch_xml(tender_xml_url(y, m, h))
        if xml is None:
            continue
        rows = parse_tender_xml(xml)
        stats = await pipe.ingest_tender_rows(rows)
        t_saved += stats.saved
        t_skip_org += stats.skipped_non_mohw
        t_skip_bl += stats.skipped_blacklist
        print(f"  {y}/{m:02d}-{h}: {len(rows):>5} 筆 → 衛福部IT候選 {stats.saved}")
    print(f"招標落庫 {t_saved}（非衛福部略過 {t_skip_org}、黑名單略過 {t_skip_bl}）")

    print(f"\n=== 決標 baseline（近 {args.award_months} 月）===")
    a_saved = a_skip_org = a_skip_bl = 0
    for y, m, h in _halfmonth_periods(args.award_months, today):
        xml = await fetch_xml(award_xml_url(y, m, h))
        if xml is None:
            continue
        rows = parse_award_xml(xml)
        stats = await pipe.ingest_award_rows(rows)
        a_saved += stats.saved
        a_skip_org += stats.skipped_non_mohw
        a_skip_bl += stats.skipped_blacklist
        print(f"  {y}/{m:02d}-{h}: {len(rows):>5} 筆 → 衛福部IT候選 {stats.saved}")
    print(f"決標落庫 {a_saved}（非衛福部略過 {a_skip_org}、黑名單略過 {a_skip_bl}）")

    fetch_log.close()
    print(f"\nbaseline 完成。下一步：g0vmcp-enrich 補 CPC 碼。")


def sync_main() -> None:
    from g0vmcp.repository import build_repositories

    parser = argparse.ArgumentParser(
        prog="g0vmcp-sync",
        description="從政府採購網半月 XML 同步標案 baseline 資料",
    )
    parser.add_argument("--db", help="SQLite DB 路徑（預設 ~/.g0vmcp/g0vmcp.db）")
    parser.add_argument("--tender-months", type=int, default=3, help="招標回溯月數")
    parser.add_argument("--award-months", type=int, default=24, help="決標回溯月數")
    args = parser.parse_args()

    db_path = _resolve_db(args.db)
    tender_repo, _ = build_repositories(db_path)
    asyncio.run(_sync(tender_repo, db_path, args))


# ── enrich ───────────────────────────────────────────────────────────────

async def _enrich(tender_repo, db_path: str, args: argparse.Namespace) -> None:
    from g0vmcp.ingestion.cf_http import CloudflareAwareHttpGetter
    from g0vmcp.ingestion.fetch_log import FetchLog
    from g0vmcp.ingestion.fetcher import PccHttpFetcher
    from g0vmcp.ingestion.pipeline import IngestionPipeline

    fetcher = PccHttpFetcher(CloudflareAwareHttpGetter())
    fetch_log = FetchLog(_resolve_fetch_log(db_path))
    pipe = IngestionPipeline(repo=tender_repo, fetch_log=fetch_log, fetcher=fetcher)

    print(f"DB: {db_path}")
    print(f"=== enrich（每批上限 {args.batch_size}）===")
    stats = await pipe.enrich(batch_size=args.batch_size)
    print(
        f"確認 IT {stats.confirmed_it} / 重分類非IT {stats.reclassified} / "
        f"無CPC碼 {stats.no_cpc} / 封鎖 {stats.blocked} / 失敗 {stats.failed}"
    )
    if stats.blocked:
        print("遭速率封鎖，已退避 4h。稍後再執行可從中斷處續補。")
    elif stats.confirmed_it or stats.reclassified or stats.no_cpc:
        print("本批完成。若仍有 llm_fallback 候選，再執行一次續補。")
    else:
        print("無待補候選（全部已確認分類）。")
    fetch_log.close()


def enrich_main() -> None:
    from g0vmcp.repository import build_repositories

    parser = argparse.ArgumentParser(
        prog="g0vmcp-enrich",
        description="從明細頁補 CPC 碼與加值欄位",
    )
    parser.add_argument("--db", help="SQLite DB 路徑（預設 ~/.g0vmcp/g0vmcp.db）")
    parser.add_argument("--batch-size", type=int, default=30, help="每批爬取上限")
    args = parser.parse_args()

    db_path = _resolve_db(args.db)
    tender_repo, _ = build_repositories(db_path)
    asyncio.run(_enrich(tender_repo, db_path, args))


# ── purge ────────────────────────────────────────────────────────────────

def purge_main() -> None:
    from g0vmcp.ingestion.scope import is_it_cpc, is_mohw

    parser = argparse.ArgumentParser(
        prog="g0vmcp-purge",
        description="移除非衛福部/非資訊服務類標案（預設 dry-run）",
    )
    parser.add_argument("--db", help="SQLite DB 路徑（預設 ~/.g0vmcp/g0vmcp.db）")
    parser.add_argument("--apply", action="store_true", help="執行（預設 dry-run）")
    args = parser.parse_args()

    db_path = _resolve_db(args.db)
    if not Path(db_path).exists():
        print(f"找不到 DB：{db_path}")
        return

    conn = sqlite3.connect(db_path)
    try:
        to_delete, to_downgrade = [], []
        rows = conn.execute(
            "SELECT tender_id, agency, title, category_code, category_method FROM tenders"
        ).fetchall()
        for tid, agency, title, code, method in rows:
            if not is_mohw(agency):
                to_delete.append((tid, agency, title, "非衛福部"))
            elif code == "pcc-tender":
                to_downgrade.append((tid, title))
            elif method == "official_code" and not is_it_cpc(code):
                to_delete.append((tid, agency, title, f"CPC {code!r} 非資訊服務類"))

        total = conn.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]
        print(f"DB: {db_path}")
        print(f"現有 {total} 筆。將刪除 {len(to_delete)} / 降級 {len(to_downgrade)}")

        if not args.apply:
            for tid, agency, title, reason in to_delete:
                print(f"  DEL {tid:<22} {reason:<18} {title[:30]}")
            for tid, title in to_downgrade:
                print(f"  DWN {tid:<22} {title[:40]}")
            print("（dry-run。加 --apply 執行）")
            return

        backup = Path(db_path).with_suffix(
            f".db.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy(db_path, backup)
        print(f"已備份 → {backup.name}")

        for tid, *_ in to_delete:
            conn.execute("DELETE FROM vendor_awards WHERE tender_id=?", (tid,))
            conn.execute("DELETE FROM announcements WHERE tender_id=?", (tid,))
            conn.execute("DELETE FROM tenders WHERE tender_id=?", (tid,))
        for tid, _ in to_downgrade:
            conn.execute(
                "UPDATE tenders SET category_code='', category_name='', "
                "domain_tag='IT', category_method='llm_fallback' WHERE tender_id=?",
                (tid,),
            )
        conn.commit()
        remaining = conn.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]
        print(f"完成。刪除 {len(to_delete)}、降級 {len(to_downgrade)}；剩 {remaining} 筆。")
    finally:
        conn.close()
