"""共享領域契約 — 三條任務的整合邊界。

所有層一律 import 此處型別,確保平行開發不發散。
對照 spec/erm.dbml 與 spec/event-storming.md。

分層約定:
  - contracts.py 只放「資料契約(DTO) + DI Protocol + Enum」,**不含行為**。
  - 富行為的聚合(append_announcement 等不變量邏輯)屬任務② 的 domain/ 模組,
    不得回頭修改本檔,以免破壞平行開發的整合邊界。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional, Protocol, Sequence, runtime_checkable


# --------------------------------------------------------------------------
# Enums (對照 erm.dbml)
# --------------------------------------------------------------------------
class TenderState(str, Enum):
    TENDERING = "TENDERING"
    AMENDED = "AMENDED"
    AWARDED = "AWARDED"
    FAILED = "FAILED"
    STALE = "STALE"       # 超過 180 天無決標公告 → 自動標記


class AnnouncementType(str, Enum):
    TENDER = "招標公告"
    AMENDMENT = "更正公告"
    AWARD = "決標公告"
    FAILURE = "無法決標公告"


class FetchStatus(str, Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"  # 被 Cloudflare/反爬擋下 → 觸發退避


# --------------------------------------------------------------------------
# Value Objects
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class TenderId:
    """自然鍵 (org_id, job_number)。"""
    org_id: str
    job_number: str

    def __str__(self) -> str:
        return f"{self.org_id}:{self.job_number}"


@dataclass(frozen=True)
class Money:
    amount: int
    currency: str = "TWD"


@dataclass(frozen=True)
class Category:
    code: str            # 官方標的分類碼
    name: str
    domain_tag: str      # 衍生領域標籤: IT / 工程 / 醫療 ...
    method: str = "official_code"  # official_code | llm_fallback


@dataclass(frozen=True)
class ProcurementProfile:
    attr: Optional[str] = None   # 工程類 / 財物類 / 勞務類
    type: Optional[str] = None   # 公開招標 / 限制性招標 ...
    way: Optional[str] = None    # 最低標 / 最有利標 ...


@dataclass(frozen=True)
class AwardedVendor:
    tax_id: str
    name: str
    award_price: int


# --------------------------------------------------------------------------
# DTOs
# --------------------------------------------------------------------------
@dataclass
class ParsedDetail:
    """任務① 擷取層的輸出 → 任務② 消費。對應 TenderDetailParsed 事件 payload。

    budget/open_date/bid_deadline/base_price/bidder_count/category_code 即本系統
    相對 pcc-tender 的「加值欄位」,全部來自明細頁擷取。
    """
    tender_id: TenderId
    title: str
    agency: str
    ann_type: AnnouncementType
    ann_date: date
    tender_seq: str = "01"
    notice_date: Optional[date] = None
    # --- 加值欄位 (pcc-tender 缺) ---
    budget: Optional[int] = None
    open_date: Optional[datetime] = None
    bid_deadline: Optional[datetime] = None
    base_price: Optional[int] = None
    bidder_count: Optional[int] = None
    category_code: Optional[str] = None
    # --- 採購性質 ---
    procurement: ProcurementProfile = field(default_factory=ProcurementProfile)
    # --- 決標公告才有 ---
    award_price: Optional[int] = None
    vendors: list[AwardedVendor] = field(default_factory=list)
    # --- 來源 ---
    source_url: Optional[str] = None
    raw: dict = field(default_factory=dict)


@dataclass
class Announcement:
    """Entity within Tender。一筆公告 = 生命週期上的一個事件點。"""
    ann_type: AnnouncementType
    ann_date: date
    tender_seq: str
    payload: dict = field(default_factory=dict)
    notice_date: Optional[date] = None
    source_url: Optional[str] = None


@dataclass
class Tender:
    """Aggregate Root 的資料快照(DTO)。

    富行為(append_announcement / 不變量檢查 / 狀態重算)由任務② 的 domain/ 實作,
    Repository 對外回傳/接收此 DTO。
    """
    tender_id: TenderId
    agency: str
    title: str
    state: TenderState = TenderState.TENDERING
    announcements: list[Announcement] = field(default_factory=list)
    budget: Optional[Money] = None
    open_date: Optional[datetime] = None
    bid_deadline: Optional[datetime] = None
    base_price: Optional[Money] = None
    bidder_count: Optional[int] = None
    category: Optional[Category] = None
    procurement: ProcurementProfile = field(default_factory=ProcurementProfile)


@dataclass(frozen=True)
class VendorAward:
    vendor_tax_id: str
    vendor_name: str
    tender_id: TenderId
    award_price: Money
    awarded_at: date


# --------------------------------------------------------------------------
# DI Protocols (介面 — 跨任務邊界)
# --------------------------------------------------------------------------
@runtime_checkable
class PccDetailFetcher(Protocol):
    """任務① 實作。從 web.pcc.gov.tw 明細頁擷取完整欄位。"""

    async def resolve_org_id(self, job_number: str, agency: str) -> Optional[str]:
        """case_no 缺機關代碼時反查 org_id;失敗回 None(呼叫端跳過,不中斷整批)。"""
        ...

    async def fetch_detail(self, job_number: str, org_id: Optional[str]) -> ParsedDetail:
        """擷取單筆明細。被反爬擋下時應拋 BlockedError 供呼叫端退避。"""
        ...


@runtime_checkable
class TenderRepository(Protocol):
    """任務② 實作;任務③ 消費。"""

    async def get(self, tender_id: str) -> Optional[Tender]: ...

    async def save(self, tender: Tender) -> None: ...

    async def search(
        self,
        *,
        keyword: Optional[str] = None,
        domain_tag: Optional[str] = None,
        agency: Optional[str] = None,
        state: Optional[str] = None,
        budget_min: Optional[int] = None,
        budget_max: Optional[int] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        limit: int = 50,
    ) -> Sequence[Tender]: ...


@runtime_checkable
class VendorRepository(Protocol):
    """任務② 實作;任務③ 消費。"""

    async def awards_of(self, tax_id: str) -> Sequence[VendorAward]: ...


class BlockedError(RuntimeError):
    """擷取被 Cloudflare/反爬擋下 → 呼叫端應指數退避。"""
