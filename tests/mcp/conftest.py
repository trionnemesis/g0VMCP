"""In-memory fake repo(實作 contracts Protocol)+ 測試資料 fixtures。

Why fake:任務③ 對 TenderRepository/VendorRepository Protocol 寫,
不依賴任務② 的 SQLite 實作,測試自帶 in-memory 替身。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Sequence

import pytest

from g0vmcp.contracts import (
    Announcement,
    AnnouncementType,
    Category,
    Money,
    Tender,
    TenderId,
    TenderState,
    VendorAward,
)
from g0vmcp.mcp_server.service import TenderQueryService


class FakeTenderRepository:
    """實作 TenderRepository Protocol。"""

    def __init__(self, tenders: Sequence[Tender]) -> None:
        self._by_key: dict[str, Tender] = {str(t.tender_id): t for t in tenders}

    async def get(self, tender_id: str) -> Optional[Tender]:
        return self._by_key.get(tender_id)

    async def save(self, tender: Tender) -> None:
        self._by_key[str(tender.tender_id)] = tender

    async def search(
        self,
        *,
        keyword: Optional[str] = None,
        domain_tag: Optional[str] = None,
        agency: Optional[str] = None,
        budget_min: Optional[int] = None,
        budget_max: Optional[int] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        limit: int = 50,
    ) -> Sequence[Tender]:
        results: list[Tender] = []
        for t in self._by_key.values():
            if keyword is not None and keyword not in t.title:
                continue
            if domain_tag is not None and (
                t.category is None or t.category.domain_tag != domain_tag
            ):
                continue
            if agency is not None and t.agency != agency:
                continue
            budget_amount = t.budget.amount if t.budget else None
            if budget_min is not None and (
                budget_amount is None or budget_amount < budget_min
            ):
                continue
            if budget_max is not None and (
                budget_amount is None or budget_amount > budget_max
            ):
                continue
            results.append(t)
        return results[:limit]


class FakeVendorRepository:
    """實作 VendorRepository Protocol。"""

    def __init__(self, awards: dict[str, Sequence[VendorAward]]) -> None:
        self._by_tax_id = awards

    async def awards_of(self, tax_id: str) -> Sequence[VendorAward]:
        return self._by_tax_id.get(tax_id, [])


def _case(case_no: str) -> TenderId:
    org_id, job_number = case_no.split(":") if ":" in case_no else ("3.79", case_no)
    return TenderId(org_id=org_id, job_number=job_number)


@pytest.fixture
def tender_it_big() -> Tender:
    """IT、budget 5,000,000、含招標+決標兩公告。case_no = 3.79:1130108-5"""
    tid = _case("3.79:1130108-5")
    return Tender(
        tender_id=tid,
        agency="衛生福利部",
        title="醫療資訊系統建置案",
        state=TenderState.AWARDED,
        announcements=[
            # 故意倒序放入,驗證 service 會依日期排序
            Announcement(
                ann_type=AnnouncementType.AWARD,
                ann_date=date(2024, 3, 15),
                tender_seq="01",
            ),
            Announcement(
                ann_type=AnnouncementType.TENDER,
                ann_date=date(2024, 1, 8),
                tender_seq="01",
            ),
        ],
        budget=Money(amount=5_000_000),
        open_date=datetime(2024, 1, 20, 9, 30),
        bid_deadline=datetime(2024, 1, 19, 17, 0),
        base_price=Money(amount=4_500_000),
        bidder_count=3,
        category=Category(code="3399", name="其他電腦服務", domain_tag="IT"),
    )


@pytest.fixture
def tender_it_small() -> Tender:
    """IT、budget 500,000(低於門檻)。"""
    return Tender(
        tender_id=_case("3.79:1130200-1"),
        agency="衛生福利部",
        title="網站維護小案",
        state=TenderState.TENDERING,
        budget=Money(amount=500_000),
        category=Category(code="3399", name="其他電腦服務", domain_tag="IT"),
    )


@pytest.fixture
def tender_construction() -> Tender:
    """工程、budget 8,000,000(非 IT)。"""
    return Tender(
        tender_id=_case("3.80:1130300-2"),
        agency="交通部",
        title="道路拓寬工程",
        state=TenderState.TENDERING,
        budget=Money(amount=8_000_000),
        category=Category(code="C1", name="道路工程", domain_tag="工程"),
    )


@pytest.fixture
def vendor_awards() -> dict[str, list[VendorAward]]:
    tax_id = "12345678"
    tid = _case("3.79:1130108-5")
    return {
        tax_id: [
            VendorAward(
                vendor_tax_id=tax_id,
                vendor_name="某資訊公司",
                tender_id=tid,
                award_price=Money(amount=4_800_000),
                awarded_at=date(2024, 3, 15),
            ),
            VendorAward(
                vendor_tax_id=tax_id,
                vendor_name="某資訊公司",
                tender_id=_case("3.81:1130400-1"),
                award_price=Money(amount=1_200_000),
                awarded_at=date(2024, 4, 1),
            ),
            VendorAward(
                vendor_tax_id=tax_id,
                vendor_name="某資訊公司",
                tender_id=_case("3.82:1130500-9"),
                award_price=Money(amount=2_000_000),
                awarded_at=date(2024, 5, 10),
            ),
        ]
    }


@pytest.fixture
def service(
    tender_it_big: Tender,
    tender_it_small: Tender,
    tender_construction: Tender,
    vendor_awards: dict[str, list[VendorAward]],
) -> TenderQueryService:
    tender_repo = FakeTenderRepository(
        [tender_it_big, tender_it_small, tender_construction]
    )
    vendor_repo = FakeVendorRepository(vendor_awards)
    return TenderQueryService(tender_repo, vendor_repo)
