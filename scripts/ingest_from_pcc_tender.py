#!/usr/bin/env python3
"""pcc-tender 開放資料 → g0vmcp.db（不經過 web 爬蟲）。

pcc-tender 決標公告欄位直接對應 Tender DTO，跳過 readBulletion（已需要登入）。
使用方式: python3.11 scripts/ingest_from_pcc_tender.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from g0vmcp.contracts import (
    Announcement,
    AnnouncementType,
    Category,
    Money,
    ProcurementProfile,
    Tender,
    TenderId,
    TenderState,
)
from g0vmcp.repository import build_repositories

DB_PATH = str(Path(__file__).resolve().parents[1] / "g0vmcp.db")

# pcc-tender 決標公告（由 twinkle-hub 查得）
PCC_ROWS = [
    {
        "job_number": "6331264701",
        "agency": "台灣電力股份有限公司核能火力發電工程處",
        "title": "通霄電廠第二期更新改建計畫複循環發電機組設備及廠房與相關設施採購帶安裝案",
        "award_price": 162445486455,
        "companies": "Mitsubishi Power, Ltd",
        "procurement_attr": "財物類",
        "procurement_type": "選擇性招標(個案)",
        "award_way": "最有利標",
        "date": "2025-08-27",
        "notice_date": "2025-09-04",
    },
    {
        "job_number": "6781453901",
        "agency": "台灣電力股份有限公司核能火力發電工程處協和施工處",
        "title": "協和電廠更新改建計畫-防波堤暨圍堤造地及LNG 卸收碼頭海事工程",
        "award_price": 42820000000,
        "companies": "皇昌營造股份有限公司",
        "procurement_attr": "工程類",
        "procurement_type": "公開招標",
        "award_way": "最有利標",
        "date": "2026-03-06",
        "notice_date": "2026-03-24",
    },
    {
        "job_number": "GDA1326001",
        "agency": "台灣中油股份有限公司",
        "title": "永安至通霄第二條海底輸氣管線海域統包工程",
        "award_price": 41107542914,
        "companies": "Boskalis Offshore Subsea Contracting B.V.",
        "procurement_attr": "工程類",
        "procurement_type": "公開招標",
        "award_way": "最有利標",
        "date": "2025-07-28",
        "notice_date": "2025-08-11",
    },
    {
        "job_number": "CF710",
        "agency": "臺北市政府捷運工程局第一區工程處",
        "title": "環狀線東環段Y29站尾軌（不含）~Y33站（不含）土木建築及水電環控區段標工程",
        "award_price": 27987600000,
        "companies": "皇昌營造股份有限公司",
        "procurement_attr": "工程類",
        "procurement_type": "公開招標",
        "award_way": "最有利標",
        "date": "2025-10-29",
        "notice_date": "2025-11-13",
    },
    {
        "job_number": "0081310001",
        "agency": "台灣電力股份有限公司",
        "title": "核一廠及核二廠用過核子燃料室內乾式貯存設施採購案",
        "award_price": 21563814226,
        "companies": "HOLTEC INTERNATIONAL",
        "procurement_attr": "財物類",
        "procurement_type": "公開招標",
        "award_way": "最有利標",
        "date": "2025-12-05",
        "notice_date": "2025-12-08",
    },
    {
        "job_number": "113A050P023",
        "agency": "交通部高速公路局",
        "title": "國道8號台南系統交流道改善及跨南133線路口立體化工程(第I810R標)",
        "award_price": 2837880000,
        "companies": "宏義工程股份有限公司",
        "procurement_attr": "工程類",
        "procurement_type": "公開招標",
        "award_way": "最有利標",
        "date": "2025-05-02",
        "notice_date": "2025-05-20",
    },
    {
        "job_number": "115CC0001",
        "agency": "交通部公路局蘇花公路改善工程處",
        "title": "115年度公務車輛租賃3年財物採購",
        "award_price": 9756936,
        "companies": None,
        "procurement_attr": "財物類",
        "procurement_type": "公開招標",
        "award_way": "最低標",
        "date": "2026-03-31",
        "notice_date": None,
    },
    {
        "job_number": "UM14N260",
        "agency": "國立臺灣大學醫學院附設醫院",
        "title": "114-117年總分院手術室2項醫材開口合約招標案",
        "award_price": 41464500,
        "companies": None,
        "procurement_attr": "財物類",
        "procurement_type": "公開招標",
        "award_way": "最低標",
        "date": "2026-03-31",
        "notice_date": None,
    },
]


def _infer_domain(attr: str | None) -> str:
    if attr == "工程類":
        return "工程"
    if attr == "財物類":
        return "財物"
    if attr == "勞務類":
        return "勞務"
    return "其他"


def _row_to_tender(row: dict) -> Tender:
    ann_date = date.fromisoformat(row["date"])
    notice_date = date.fromisoformat(row["notice_date"]) if row.get("notice_date") else None

    vendor_list = []
    if row.get("companies"):
        # pcc-tender companies 是文字，沒有 tax_id；用空字串占位
        vendor_list = [{"tax_id": "", "name": row["companies"], "award_price": row["award_price"]}]

    payload: dict = {}
    if vendor_list:
        payload["vendors"] = vendor_list

    ann = Announcement(
        ann_type=AnnouncementType.AWARD,
        ann_date=ann_date,
        tender_seq="01",
        notice_date=notice_date,
        source_url=None,
        payload=payload,
    )

    domain = _infer_domain(row.get("procurement_attr"))
    category = Category(
        code="pcc-tender",
        name=row.get("procurement_attr", ""),
        domain_tag=domain,
    )

    return Tender(
        tender_id=TenderId(org_id="", job_number=row["job_number"]),
        agency=row["agency"],
        title=row["title"],
        state=TenderState.AWARDED,
        announcements=[ann],
        budget=Money(row["award_price"]) if row.get("award_price") else None,
        open_date=None,
        bid_deadline=None,
        base_price=None,
        bidder_count=None,
        category=category,
        procurement=ProcurementProfile(
            attr=row.get("procurement_attr"),
            type=row.get("procurement_type"),
            way=row.get("award_way"),
        ),
    )


async def main(tender_repo) -> None:
    # clear existing data
    await tender_repo._conn.execute("DELETE FROM vendor_awards")
    await tender_repo._conn.execute("DELETE FROM vendors")
    await tender_repo._conn.execute("DELETE FROM announcements")
    await tender_repo._conn.execute("DELETE FROM tenders")
    await tender_repo._conn.commit()
    print(f"清除舊資料，準備寫入 {len(PCC_ROWS)} 筆")

    for row in PCC_ROWS:
        tender = _row_to_tender(row)
        await tender_repo.save(tender)
        print(f"  saved {tender.tender_id!s:<30} {tender.agency[:30]}")

    print("\n=== DB 現況 ===")
    results = await tender_repo.search(limit=20)
    print(f"共 {len(results)} 筆")
    for t in results:
        bgt = f"{t.budget.amount:,}" if t.budget else "-"
        print(
            f"  {t.tender_id!s:<35} {t.state.value:<12} "
            f"預算={bgt:<18} {t.agency[:25]}"
        )


if __name__ == "__main__":
    tender_repo, _ = build_repositories(DB_PATH)
    asyncio.run(main(tender_repo))
