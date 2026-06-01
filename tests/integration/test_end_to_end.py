"""端到端整合:build_repositories(真實 SQLite) → repository → service 三層拼合。

三個 agent 各自交付的層,在此首次合跑。刻意用「同步測試 + 多次 asyncio.run」,
順帶驗證 aiosqlite conn 跨 event loop 可用(即 build 在暫時 loop 建 conn、
mcp.run 在另一 loop 使用 的真實開機場景)。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime

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
from g0vmcp.mcp_server.service import TenderQueryService
from g0vmcp.repository import build_repositories


def _sample_tender() -> Tender:
    """一筆已決標的 IT 標案,含預算/開標等 pcc-tender 缺的加值欄位 + 得標廠商。"""
    return Tender(
        tender_id=TenderId("3.80.11", "1130108-5"),
        agency="測試機關",
        title="資訊系統建置案",
        state=TenderState.AWARDED,
        announcements=[
            Announcement(AnnouncementType.TENDER, date(2024, 5, 10), "01", payload={}),
            Announcement(
                AnnouncementType.AWARD,
                date(2024, 6, 10),
                "01",
                payload={
                    "vendors": [
                        {"tax_id": "12345678", "name": "某資訊公司", "award_price": 3628000}
                    ]
                },
            ),
        ],
        budget=Money(5_000_000),
        open_date=datetime(2024, 5, 20, 14, 30),
        bid_deadline=datetime(2024, 5, 19, 17, 0),
        bidder_count=3,
        category=Category(code="C3399", name="資訊服務", domain_tag="IT"),
        procurement=ProcurementProfile(attr="財物類", type="公開招標", way="最低標"),
    )


def test_end_to_end_wiring(tmp_path):
    db = str(tmp_path / "g0v.db")
    tender_repo, vendor_repo = build_repositories(db)  # event loop #1 (建 conn)
    service = TenderQueryService(tender_repo, vendor_repo)

    asyncio.run(tender_repo.save(_sample_tender()))  # loop #2 — 跨 loop 使用同一 conn

    detail = asyncio.run(service.get_tender_detail("3.80.11:1130108-5"))
    assert detail is not None
    # pcc-tender 缺、本系統補上的加值欄位
    assert detail.budget == 5_000_000
    assert detail.open_date == datetime(2024, 5, 20, 14, 30)
    assert detail.category_code == "C3399"
    assert detail.domain_tag == "IT"

    results = asyncio.run(
        service.search_tenders(domain_tag="IT", budget_min=1_000_000)
    )
    assert [r.case_no for r in results] == ["3.80.11:1130108-5"]

    timeline = asyncio.run(service.get_tender_lifecycle("3.80.11:1130108-5"))
    assert [e.ann_type for e in timeline] == ["招標公告", "決標公告"]

    awards = asyncio.run(service.get_vendor_awards("12345678"))
    assert len(awards) == 1
    assert awards[0].award_price == 3628000
    assert awards[0].case_no == "3.80.11:1130108-5"

    # 查無 → None,不拋例外
    assert asyncio.run(service.get_tender_detail("0.0.0:nope")) is None
