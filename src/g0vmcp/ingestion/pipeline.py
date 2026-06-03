"""兩階段 ingestion 編排 — baseline 落庫(關鍵字暫定 IT)→ enrich 補 CPC 碼確認。

階段一 ingest_*_rows:半月 XML 解析後的 row → scope 過濾(is_mohw + keyword_prescreen
黑名單剔除)→ 暫定 Category(domain_tag='IT', method='llm_fallback')落庫,寫 fetch_log
PENDING。不碰網路,可純測。

階段二 enrich:對 method='llm_fallback' 的候選逐筆抓明細頁,以官方標的分類 CPC 碼
重新分類(45/84/47→IT;非 IT→留待 purge 剔除),並補預算/開標等加值欄位。**保留既有
announcements**(決標廠商不被洗掉),僅合併分類與加值欄位。被反爬擋下 → BLOCKED 退避並
中止本批,fetch_log.retry_after 支撐漸進補完。

DI 注入 fetcher(PccDetailFetcher)/repo(TenderRepository)/fetch_log;測試以 fake 替身。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from g0vmcp.contracts import (
    Announcement,
    AnnouncementType,
    BlockedError,
    Category,
    FetchStatus,
    Money,
    PccDetailFetcher,
    ProcurementProfile,
    Tender,
    TenderId,
    TenderRepository,
    TenderState,
)
from g0vmcp.ingestion.fetch_log import FetchLog
from g0vmcp.ingestion.mappers import detail_to_tender
from g0vmcp.ingestion.opendata import AwardRow, OpenDataRow
from g0vmcp.ingestion.scope import is_it_cpc, is_mohw, keyword_prescreen

_BLOCKED_RETRY_HOURS = 4


def _parse_slash_date(text: str) -> Optional[date]:
    """民國/西元斜線日期 '2026/04/20' → date。空或格式異常回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text.replace("/", "-"))
    except ValueError:
        return None


def _baseline_category() -> Category:
    """baseline 暫定:關鍵字判為 IT 候選,待明細頁 CPC 碼確認(llm_fallback + needs review)。"""
    return Category(code="", name="", domain_tag="IT", method="llm_fallback")


@dataclass
class BaselineStats:
    saved: int = 0
    skipped_non_mohw: int = 0
    skipped_blacklist: int = 0
    skipped_bad_date: int = 0


@dataclass
class EnrichStats:
    confirmed_it: int = 0      # CPC 確認為 IT(official_code)
    reclassified: int = 0      # CPC 非 IT(false positive,待 purge 剔除)
    no_cpc: int = 0            # 明細頁無 CPC 碼,維持 llm_fallback
    blocked: int = 0
    failed: int = 0


class IngestionPipeline:
    """兩階段 ingestion 編排。"""

    def __init__(
        self,
        *,
        repo: TenderRepository,
        fetch_log: FetchLog,
        fetcher: Optional[PccDetailFetcher] = None,
    ) -> None:
        self._repo = repo
        self._fetch_log = fetch_log
        self._fetcher = fetcher

    # ------------------------------------------------------------------
    # 階段一:baseline(scope 過濾 + 暫定分類落庫)
    # ------------------------------------------------------------------
    def _passes_scope(self, org_name: str, title: str, stats: BaselineStats) -> bool:
        if not is_mohw(org_name):
            stats.skipped_non_mohw += 1
            return False
        if keyword_prescreen(title) == "blacklist":
            stats.skipped_blacklist += 1
            return False
        return True

    async def ingest_tender_rows(self, rows: list[OpenDataRow]) -> BaselineStats:
        """招標(進行中)半月 XML → TENDERING baseline。"""
        stats = BaselineStats()
        for row in rows:
            if not self._passes_scope(row.org_name, row.title, stats):
                continue
            ann_date = _parse_slash_date(row.ann_date)
            if ann_date is None:
                stats.skipped_bad_date += 1
                continue
            tender = Tender(
                tender_id=TenderId(org_id="", job_number=row.case_no),
                agency=row.org_name,
                title=row.title,
                state=TenderState.TENDERING,
                announcements=[
                    Announcement(
                        ann_type=AnnouncementType.TENDER,
                        ann_date=ann_date,
                        tender_seq="01",
                        payload={},
                    )
                ],
                category=_baseline_category(),
                procurement=ProcurementProfile(
                    attr=row.procurement_attr or None,
                    type=row.procurement_type or None,
                ),
            )
            await self._repo.save(tender)
            self._fetch_log.record(row.case_no, FetchStatus.PENDING)
            stats.saved += 1
        return stats

    async def ingest_award_rows(self, rows: list[AwardRow]) -> BaselineStats:
        """決標(近 2 年)半月 XML → AWARDED baseline,得標廠商進 payload。"""
        stats = BaselineStats()
        for row in rows:
            if not self._passes_scope(row.org_name, row.title, stats):
                continue
            ann_date = _parse_slash_date(row.award_date)
            if ann_date is None:
                stats.skipped_bad_date += 1
                continue
            price = int(row.award_price) if row.award_price.isdigit() else None
            vendors = [
                {"tax_id": "", "name": name, "award_price": price}
                for name in row.winners
                if price is not None
            ]
            payload: dict = {"vendors": vendors} if vendors else {}
            tender = Tender(
                tender_id=TenderId(org_id="", job_number=row.case_no),
                agency=row.org_name,
                title=row.title,
                state=TenderState.AWARDED,
                announcements=[
                    Announcement(
                        ann_type=AnnouncementType.AWARD,
                        ann_date=ann_date,
                        tender_seq="01",
                        notice_date=_parse_slash_date(row.notice_date),
                        payload=payload,
                    )
                ],
                budget=Money(price) if price is not None else None,
                category=_baseline_category(),
                procurement=ProcurementProfile(
                    attr=row.procurement_attr or None,
                    type=row.procurement_type or None,
                    way=row.award_way or None,
                ),
            )
            await self._repo.save(tender)
            self._fetch_log.record(row.case_no, FetchStatus.PENDING)
            stats.saved += 1
        return stats

    # ------------------------------------------------------------------
    # 階段二:enrich(明細頁 CPC 碼確認 + 加值欄位)
    # ------------------------------------------------------------------
    async def enrich(
        self, batch_size: int, *, now: Optional[datetime] = None
    ) -> EnrichStats:
        """對 llm_fallback(暫定 IT)候選逐筆補 CPC 碼;非 IT 重分類待 purge。"""
        if self._fetcher is None:
            raise RuntimeError("enrich 需要注入 fetcher")
        now = now or datetime.now(timezone.utc)
        stats = EnrichStats()

        candidates = await self._repo.search(domain_tag="IT", limit=1000)
        pending = [
            t
            for t in candidates
            if t.category is not None
            and t.category.method == "llm_fallback"
            and self._fetch_log.should_fetch(t.tender_id.job_number, now)
        ]

        for tender in pending[:batch_size]:
            case_no = tender.tender_id.job_number
            try:
                org_id = await self._fetcher.resolve_org_id(case_no, tender.agency)
                detail = await self._fetcher.fetch_detail(case_no, org_id)
            except BlockedError:
                self._fetch_log.record_blocked(
                    case_no, retry_after=now + timedelta(hours=_BLOCKED_RETRY_HOURS)
                )
                stats.blocked += 1
                break  # 中止本批,避免持續觸發封鎖;下次 retry_after 後續抓
            except Exception:  # noqa: BLE001 — 單筆失敗不中斷整批
                self._fetch_log.record(case_no, FetchStatus.FAILED)
                stats.failed += 1
                continue

            classified = detail_to_tender(detail)

            # 合併分類 + 加值欄位到既有 tender(保留 announcements / 決標廠商)
            if classified.category is not None:
                tender.category = classified.category
            tender.budget = classified.budget or tender.budget
            tender.open_date = classified.open_date or tender.open_date
            tender.bid_deadline = classified.bid_deadline or tender.bid_deadline
            tender.base_price = classified.base_price or tender.base_price
            if classified.bidder_count is not None:
                tender.bidder_count = classified.bidder_count
            if classified.procurement and classified.procurement.attr:
                tender.procurement = classified.procurement

            # org_id 換鍵:baseline 為空 org_id,明細頁反查補上 → 主鍵變更須先搬舊列
            old_tid = str(tender.tender_id)
            if org_id and org_id != tender.tender_id.org_id:
                new_id = TenderId(org_id=org_id, job_number=case_no)
                await self._repo.rekey(old_tid, str(new_id))
                tender.tender_id = new_id

            await self._repo.save(tender)
            self._fetch_log.record(case_no, FetchStatus.SUCCESS)

            code = tender.category.code if tender.category else ""
            if not code:
                stats.no_cpc += 1
            elif is_it_cpc(code):
                stats.confirmed_it += 1
            else:
                stats.reclassified += 1

        return stats
