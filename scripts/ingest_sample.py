#!/usr/bin/env python3
"""測試用擷取腳本: pcc-tender job_number → web.pcc.gov.tw 明細 → g0vmcp.db

使用方式: python scripts/ingest_sample.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from g0vmcp.contracts import (
    Announcement,
    AnnouncementType,
    Category,
    Money,
    ParsedDetail,
    Tender,
    TenderId,
    TenderState,
)
from g0vmcp.ingestion.fetcher import PccHttpFetcher
from g0vmcp.ingestion.http import Resp
from g0vmcp.repository import build_repositories

DB_PATH = str(Path(__file__).resolve().parents[1] / "g0vmcp.db")

# pcc-tender 取得的近期財物類決標案號
SAMPLE_CASES = [
    ("115CC0001", "交通部公路局蘇花公路改善工程處"),
    ("UM14N260", "國立臺灣大學醫學院附設醫院"),
    ("115LM0018U", "國營臺灣鐵路股份有限公司"),
]


async def _httpx_get(url: str) -> Resp:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9",
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url, headers=headers)
        return Resp(status_code=r.status_code, text=r.text, url=str(r.url))


def _state_from_ann_type(ann_type: AnnouncementType) -> TenderState:
    return {
        AnnouncementType.AWARD: TenderState.AWARDED,
        AnnouncementType.FAILURE: TenderState.FAILED,
        AnnouncementType.AMENDMENT: TenderState.AMENDED,
    }.get(ann_type, TenderState.TENDERING)


def _infer_domain(category_code: str | None, attr: str | None) -> str:
    if category_code and category_code.startswith("45"):
        return "IT"
    if attr == "工程類":
        return "工程"
    if attr == "勞務類":
        return "勞務"
    return "其他"


def _detail_to_tender(detail: ParsedDetail) -> Tender:
    vendor_list = [
        {"tax_id": v.tax_id, "name": v.name, "award_price": v.award_price}
        for v in detail.vendors
    ]
    payload: dict = {**detail.raw}
    if vendor_list:
        payload["vendors"] = vendor_list

    ann = Announcement(
        ann_type=detail.ann_type,
        ann_date=detail.ann_date,
        tender_seq=detail.tender_seq,
        notice_date=detail.notice_date,
        source_url=detail.source_url,
        payload=payload,
    )

    category = None
    if detail.category_code:
        domain = _infer_domain(detail.category_code, detail.procurement.attr)
        category = Category(
            code=detail.category_code,
            name=detail.raw.get("標的分類", ""),
            domain_tag=domain,
        )

    return Tender(
        tender_id=detail.tender_id,
        agency=detail.agency,
        title=detail.title,
        state=_state_from_ann_type(detail.ann_type),
        announcements=[ann],
        budget=Money(detail.budget) if detail.budget else None,
        open_date=detail.open_date,
        bid_deadline=detail.bid_deadline,
        base_price=Money(detail.base_price) if detail.base_price else None,
        bidder_count=detail.bidder_count,
        category=category,
        procurement=detail.procurement,
    )


async def ingest(tender_repo, cases: list[tuple[str, str]]) -> None:
    fetcher = PccHttpFetcher(_httpx_get)

    for job_number, agency in cases:
        print(f"\n[{job_number}] resolving org_id ...", end=" ", flush=True)
        try:
            org_id = await fetcher.resolve_org_id(job_number, agency)
            print(f"org_id={org_id!r}")

            print(f"[{job_number}] fetching detail ...", end=" ", flush=True)
            detail = await fetcher.fetch_detail(job_number, org_id)
            print(f"{detail.ann_type.value} | {detail.title[:40]!r}")

            tender = _detail_to_tender(detail)
            await tender_repo.save(tender)
            print(f"[{job_number}] saved  tender_id={tender.tender_id!s}")

        except Exception as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}")

    print("\n=== DB 現況 ===")
    results = await tender_repo.search(limit=20)
    print(f"共 {len(results)} 筆")
    for t in results:
        bgt = f"{t.budget.amount:,}" if t.budget else "-"
        print(
            f"  {t.tender_id!s:<38} {t.state.value:<12} "
            f"預算={bgt:<15} {t.agency[:20]}"
        )


if __name__ == "__main__":
    tender_repo, _ = build_repositories(DB_PATH)
    asyncio.run(ingest(tender_repo, SAMPLE_CASES))
