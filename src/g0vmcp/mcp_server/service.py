"""Query BC — 讀模型投影 service。

Why DI: 注入 TenderRepository / VendorRepository Protocol(任務② 實作),
service 不知道底層是 SQLite 還是 in-memory,只依賴 contracts 的介面。
業務查詢邏輯集中此處,MCP tool 僅做轉接。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from g0vmcp.contracts import (
    AnnouncementType,
    TenderRepository,
    VendorRepository,
)


# --------------------------------------------------------------------------
# 讀模型投影 DTO (對應 spec §5 Read Models)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class TenderSummaryView:
    """search_tenders 列項。"""
    case_no: str
    title: str
    agency: str
    domain_tag: Optional[str]
    budget: Optional[int]
    state: str


@dataclass(frozen=True)
class TenderDetailView:
    """get_tender_detail — 含 pcc-tender 缺的加值欄位。"""
    case_no: str
    title: str
    agency: str
    state: str
    budget: Optional[int]
    open_date: Optional[datetime]
    bid_deadline: Optional[datetime]
    base_price: Optional[int]
    bidder_count: Optional[int]
    category_code: Optional[str]
    domain_tag: Optional[str]


@dataclass(frozen=True)
class LifecycleEntryView:
    """get_tender_lifecycle 時間線單點。"""
    ann_type: str
    ann_date: date
    tender_seq: str


@dataclass(frozen=True)
class VendorAwardView:
    """get_vendor_awards 單筆得標。"""
    case_no: str
    award_price: int
    currency: str
    awarded_at: date


class TenderQueryService:
    """讀模型查詢服務。DI 注入兩個 Repository Protocol。"""

    def __init__(
        self,
        tender_repo: TenderRepository,
        vendor_repo: VendorRepository,
    ) -> None:
        self._tenders = tender_repo
        self._vendors = vendor_repo

    async def search_tenders(
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
    ) -> list[TenderSummaryView]:
        tenders = await self._tenders.search(
            keyword=keyword,
            domain_tag=domain_tag,
            agency=agency,
            budget_min=budget_min,
            budget_max=budget_max,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
        return [
            TenderSummaryView(
                case_no=str(t.tender_id),
                title=t.title,
                agency=t.agency,
                domain_tag=t.category.domain_tag if t.category else None,
                budget=t.budget.amount if t.budget else None,
                state=t.state.value,
            )
            for t in tenders
        ]

    async def get_tender_detail(self, case_no: str) -> Optional[TenderDetailView]:
        """查無 → None(不拋例外)。"""
        tender = await self._tenders.get(case_no)
        if tender is None:
            return None
        return TenderDetailView(
            case_no=str(tender.tender_id),
            title=tender.title,
            agency=tender.agency,
            state=tender.state.value,
            budget=tender.budget.amount if tender.budget else None,
            open_date=tender.open_date,
            bid_deadline=tender.bid_deadline,
            base_price=tender.base_price.amount if tender.base_price else None,
            bidder_count=tender.bidder_count,
            category_code=tender.category.code if tender.category else None,
            domain_tag=tender.category.domain_tag if tender.category else None,
        )

    async def get_tender_lifecycle(self, case_no: str) -> list[LifecycleEntryView]:
        """依公告日期排序的事件時間線。查無 → 空 list。"""
        tender = await self._tenders.get(case_no)
        if tender is None:
            return []
        ordered = sorted(tender.announcements, key=lambda a: a.ann_date)
        return [
            LifecycleEntryView(
                ann_type=(
                    a.ann_type.value
                    if isinstance(a.ann_type, AnnouncementType)
                    else str(a.ann_type)
                ),
                ann_date=a.ann_date,
                tender_seq=a.tender_seq,
            )
            for a in ordered
        ]

    async def get_vendor_awards(self, tax_id: str) -> list[VendorAwardView]:
        """廠商得標記錄。查無 → 空 list。"""
        awards = await self._vendors.awards_of(tax_id)
        return [
            VendorAwardView(
                case_no=str(a.tender_id),
                award_price=a.award_price.amount,
                currency=a.award_price.currency,
                awarded_at=a.awarded_at,
            )
            for a in awards
        ]
