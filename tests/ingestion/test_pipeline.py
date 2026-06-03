"""IngestionPipeline 兩階段編排整合測試 — fake fetcher + in-memory repo + FetchLog。

覆蓋計畫驗收點:baseline 黑名單/非衛福部剔除、enrich 後 CPC 轉 official_code 解除
llm_fallback、CPC 非 IT 重分類、決標 announcements/廠商保留、BlockedError 退避中止本批、
重跑不重抓 SUCCESS。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import aiosqlite
import pytest

from g0vmcp.contracts import (
    AnnouncementType,
    BlockedError,
    FetchStatus,
    ParsedDetail,
    ProcurementProfile,
    TenderId,
    TenderState,
)
from g0vmcp.ingestion.fetch_log import FetchLog
from g0vmcp.ingestion.opendata import AwardRow, OpenDataRow
from g0vmcp.ingestion.pipeline import IngestionPipeline
from g0vmcp.repository import SqliteTenderRepository, init_db


@pytest.fixture
async def repo():
    conn = await aiosqlite.connect(":memory:")
    await init_db(conn)
    try:
        yield SqliteTenderRepository(conn)
    finally:
        await conn.close()


@pytest.fixture
def fetch_log() -> FetchLog:
    log = FetchLog(":memory:")
    try:
        yield log
    finally:
        log.close()


class FakeFetcher:
    """實作 PccDetailFetcher:依 case_no 回預設 ParsedDetail / org_id;可模擬封鎖。"""

    def __init__(self, details=None, org_ids=None, blocked=None):
        self._details = details or {}
        self._org_ids = org_ids or {}
        self._blocked = blocked or set()

    async def resolve_org_id(self, job_number, agency):
        if job_number in self._blocked:
            raise BlockedError(f"blocked: {job_number}")
        return self._org_ids.get(job_number)

    async def fetch_detail(self, job_number, org_id):
        if job_number in self._blocked:
            raise BlockedError(f"blocked: {job_number}")
        return self._details[job_number]


def _tender_row(case_no, org_name, title, attr="勞務類"):
    return OpenDataRow(
        ann_date="2026/03/02",
        org_name=org_name,
        case_no=case_no,
        title=title,
        procurement_type="公開招標",
        procurement_attr=attr,
    )


def _detail(case_no, org_id, category_code, *, agency="衛生福利部疾病管制署"):
    return ParsedDetail(
        tender_id=TenderId(org_id=org_id, job_number=case_no),
        title="傳染病監測資訊系統維運案",
        agency=agency,
        ann_type=AnnouncementType.TENDER,
        ann_date=date(2026, 3, 2),
        budget=5_000_000,
        open_date=datetime(2026, 3, 10, 9, 30),
        category_code=category_code,
        procurement=ProcurementProfile(attr="勞務類", type="公開招標"),
    )


# ----------------------------------------------------------------------
# 階段一:baseline
# ----------------------------------------------------------------------
class TestBaseline:
    async def test_filters_non_mohw_and_blacklist(self, repo, fetch_log):
        pipe = IngestionPipeline(repo=repo, fetch_log=fetch_log)
        rows = [
            _tender_row("IT-001", "衛生福利部疾病管制署", "疫情通報資訊系統維運案"),
            _tender_row("OTHER-1", "內政部警政署", "治安資訊系統案"),       # 非衛福部
            _tender_row("BLK-1", "衛生福利部", "PCR核酸檢驗系統採購案"),     # 黑名單
        ]
        stats = await pipe.ingest_tender_rows(rows)
        assert stats.saved == 1
        assert stats.skipped_non_mohw == 1
        assert stats.skipped_blacklist == 1
        # 落庫者為 TENDERING + 暫定 IT/llm_fallback
        t = await repo.get(":IT-001")
        assert t is not None
        assert t.state is TenderState.TENDERING
        assert t.category.domain_tag == "IT"
        assert t.category.method == "llm_fallback"
        assert fetch_log.status_of("IT-001") is FetchStatus.PENDING

    async def test_award_rows_keep_winners(self, repo, fetch_log):
        pipe = IngestionPipeline(repo=repo, fetch_log=fetch_log)
        rows = [
            AwardRow(
                award_date="2026/03/16",
                notice_date="2026/03/20",
                org_name="衛生福利部中央健康保險署",
                case_no="NHI-IT-1",
                title="健保資訊系統數位轉型服務案",
                procurement_type="限制性招標",
                procurement_attr="勞務類",
                award_way="準用最有利標",
                award_price="963800000",
                winners=("資拓宏宇國際股份有限公司",),
            )
        ]
        stats = await pipe.ingest_award_rows(rows)
        assert stats.saved == 1
        t = await repo.get(":NHI-IT-1")
        assert t.state is TenderState.AWARDED
        assert t.announcements[0].payload["vendors"][0]["name"].startswith("資拓宏宇")


# ----------------------------------------------------------------------
# 階段二:enrich
# ----------------------------------------------------------------------
class TestEnrich:
    async def test_cpc_confirms_it_and_clears_fallback(self, repo, fetch_log):
        pipe = IngestionPipeline(repo=repo, fetch_log=fetch_log)
        await pipe.ingest_tender_rows(
            [_tender_row("IT-001", "衛生福利部疾病管制署", "資訊系統維運案")]
        )
        pipe._fetcher = FakeFetcher(
            details={"IT-001": _detail("IT-001", "3.80.11", "8421")},  # 84→IT
            org_ids={"IT-001": "3.80.11"},
        )
        stats = await pipe.enrich(batch_size=10)
        assert stats.confirmed_it == 1
        # 換鍵後以 org_id 鍵存在,官方碼分類,加值欄位補上
        t = await repo.get("3.80.11:IT-001")
        assert t is not None
        assert t.category.method == "official_code"
        assert t.category.domain_tag == "IT"
        assert t.budget.amount == 5_000_000
        assert t.open_date == datetime(2026, 3, 10, 9, 30)
        assert await repo.get(":IT-001") is None  # 舊空鍵已搬移
        assert fetch_log.status_of("IT-001") is FetchStatus.SUCCESS

    async def test_cpc_non_it_reclassified(self, repo, fetch_log):
        pipe = IngestionPipeline(repo=repo, fetch_log=fetch_log)
        await pipe.ingest_tender_rows(
            [_tender_row("FP-1", "衛生福利部食品藥物管理署", "醫療影像資訊系統建置案")]
        )
        # 標題過白名單(含「資訊/系統」),但 CPC 5159 屬醫療 → 重分類,待 purge
        pipe._fetcher = FakeFetcher(
            details={"FP-1": _detail("FP-1", "3.9", "5159")},
            org_ids={"FP-1": "3.9"},
        )
        stats = await pipe.enrich(batch_size=10)
        assert stats.reclassified == 1
        t = await repo.get("3.9:FP-1")
        assert t.category.domain_tag != "IT"
        assert t.category.method == "official_code"

    async def test_blocked_sets_retry_after_and_stops_batch(self, repo, fetch_log):
        pipe = IngestionPipeline(repo=repo, fetch_log=fetch_log)
        await pipe.ingest_tender_rows(
            [
                _tender_row("B-1", "衛生福利部", "資訊系統A"),
                _tender_row("B-2", "衛生福利部", "資訊系統B"),
            ]
        )
        pipe._fetcher = FakeFetcher(blocked={"B-1", "B-2"})
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        stats = await pipe.enrich(batch_size=10, now=now)
        assert stats.blocked == 1  # 第一筆封鎖即 break
        # 被封鎖者進入退避,retry_after 未到 → 不再抓
        first = "B-1" if fetch_log.status_of("B-1") is FetchStatus.BLOCKED else "B-2"
        assert fetch_log.should_fetch(first, now + timedelta(hours=1)) is False
        assert fetch_log.should_fetch(first, now + timedelta(hours=5)) is True

    async def test_rerun_skips_succeeded(self, repo, fetch_log):
        pipe = IngestionPipeline(repo=repo, fetch_log=fetch_log)
        await pipe.ingest_tender_rows(
            [_tender_row("IT-001", "衛生福利部", "資訊系統維運案")]
        )
        fetcher = FakeFetcher(
            details={"IT-001": _detail("IT-001", "3.80.11", "8421")},
            org_ids={"IT-001": "3.80.11"},
        )
        pipe._fetcher = fetcher
        await pipe.enrich(batch_size=10)
        # 第二輪:已 SUCCESS 且轉 official_code → 不在候選,不重抓
        stats2 = await pipe.enrich(batch_size=10)
        assert stats2.confirmed_it == 0
        assert stats2.no_cpc == 0
