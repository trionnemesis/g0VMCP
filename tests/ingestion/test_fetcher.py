"""驗收 spec/features/tender-ingestion.feature 的 4 個場景。"""
from __future__ import annotations

from datetime import datetime

import pytest

from g0vmcp.contracts import (
    AnnouncementType,
    BlockedError,
    FetchStatus,
    PccDetailFetcher,
)
from g0vmcp.ingestion.backoff import ExponentialBackoff
from g0vmcp.ingestion.fetch_log import FetchLog, detail_target
from g0vmcp.ingestion.fetcher import PccHttpFetcher
from g0vmcp.ingestion.http import Resp

from .conftest import FakeHttp, load_fixture


def test_fetcher_satisfies_protocol(fake_http: FakeHttp) -> None:
    assert isinstance(PccHttpFetcher(fake_http), PccDetailFetcher)


# ----------------------------------------------------------------------
# 場景 1: 從明細頁解析出 pcc-tender 缺漏的加值欄位
# ----------------------------------------------------------------------
class TestScenarioParseValueAddedFields:
    async def test_parse_tender_detail_extracts_budget_open_date_category(
        self, fake_http: FakeHttp
    ) -> None:
        fake_http.default(Resp(status_code=200, text=load_fixture("tender_detail.html")))
        fetcher = PccHttpFetcher(fake_http)

        detail = await fetcher.fetch_detail("1130108-5", "3.80.11")

        # TenderDetailParsed payload 應包含加值欄位
        assert detail.budget == 12_500_000
        assert detail.open_date == datetime(2025, 1, 20, 14, 30)
        assert detail.bid_deadline == datetime(2025, 1, 20, 12, 0)
        assert detail.category_code == "4523"
        assert detail.tender_id.org_id == "3.80.11"
        assert detail.tender_id.job_number == "1130108-5"
        assert detail.ann_type is AnnouncementType.TENDER
        assert detail.procurement.attr == "財物類"
        assert detail.procurement.type == "公開招標"
        assert detail.procurement.way == "最有利標"

    async def test_parse_award_detail_extracts_price_and_vendors(
        self, fake_http: FakeHttp
    ) -> None:
        fake_http.default(Resp(status_code=200, text=load_fixture("award_detail.html")))
        fetcher = PccHttpFetcher(fake_http)

        detail = await fetcher.fetch_detail("1130108-5", "3.80.11")

        assert detail.ann_type is AnnouncementType.AWARD
        assert detail.award_price == 10_800_000
        assert detail.bidder_count == 3
        assert detail.base_price == 11_000_000
        assert len(detail.vendors) == 2
        first = detail.vendors[0]
        assert first.name == "泰安資安股份有限公司"
        assert first.tax_id == "24536789"
        assert first.award_price == 6_800_000


# ----------------------------------------------------------------------
# 場景 2: 案號缺機關代碼時須先反查 org_id;失敗則跳過不中斷
# ----------------------------------------------------------------------
class TestScenarioResolveOrgId:
    async def test_resolve_org_id_from_search_result(self, fake_http: FakeHttp) -> None:
        fake_http.default(Resp(status_code=200, text=load_fixture("search_result.html")))
        fetcher = PccHttpFetcher(fake_http)

        org_id = await fetcher.resolve_org_id("1130108-5", "內政部警政署")

        assert org_id == "3.80.11"

    async def test_resolve_org_id_returns_none_when_not_found(
        self, fake_http: FakeHttp
    ) -> None:
        fake_http.default(Resp(status_code=200, text="<html><body>查無資料</body></html>"))
        fetcher = PccHttpFetcher(fake_http)

        org_id = await fetcher.resolve_org_id("9999999-9", "不存在的機關")

        assert org_id is None

    async def test_batch_skips_failed_resolution_without_aborting(
        self, fake_http: FakeHttp
    ) -> None:
        """反查失敗該筆標記 FAILED 並跳過,整批其餘案號續抓。"""
        good_html = load_fixture("search_result.html")

        def is_good(url: str) -> bool:
            return "1130108-5" in url

        fake_http.when(is_good, Resp(status_code=200, text=good_html))
        fake_http.default(Resp(status_code=200, text="<html>查無資料</html>"))

        fetcher = PccHttpFetcher(fake_http)
        log = FetchLog()
        batch = [("1130108-5", "內政部警政署"), ("9999999-9", "不存在的機關")]
        resolved: list[str] = []

        for job_number, agency in batch:
            org_id = await fetcher.resolve_org_id(job_number, agency)
            target = detail_target(job_number, org_id or "?")
            if org_id is None:
                log.record(target, FetchStatus.FAILED)
                continue  # 跳過,不中斷整批
            resolved.append(org_id)

        assert resolved == ["3.80.11"]  # 整批未中斷,好的那筆仍解析成功
        failed_target = detail_target("9999999-9", "?")
        assert log.status_of(failed_target) is FetchStatus.FAILED


# ----------------------------------------------------------------------
# 場景 3: 被反爬擋下時退避而非重試風暴
# ----------------------------------------------------------------------
class TestScenarioCloudflareBlocked:
    @pytest.mark.parametrize("http_status", [403, 429])
    async def test_blocked_status_raises_blocked_error(
        self, fake_http: FakeHttp, http_status: int
    ) -> None:
        fake_http.default(Resp(status_code=http_status, text="Cloudflare"))
        fetcher = PccHttpFetcher(fake_http)

        with pytest.raises(BlockedError):
            await fetcher.fetch_detail("1130108-5", "3.80.11")

    async def test_blocked_target_marked_blocked_and_backs_off_exponentially(
        self, fake_http: FakeHttp
    ) -> None:
        fake_http.default(Resp(status_code=403, text="Cloudflare"))
        fetcher = PccHttpFetcher(fake_http)
        log = FetchLog()

        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        backoff = ExponentialBackoff(base=1.0, factor=2.0, sleep=fake_sleep)
        target = detail_target("1130108-5", "3.80.11")

        with pytest.raises(BlockedError):
            await fetcher.fetch_detail("1130108-5", "3.80.11")
        log.record(target, FetchStatus.BLOCKED, http_status=403)

        # 退避而非立即高頻重試:連續等待秒數呈指數成長
        await backoff.wait()
        await backoff.wait()
        await backoff.wait()

        assert log.status_of(target) is FetchStatus.BLOCKED
        assert slept == [1.0, 2.0, 4.0]  # 1 → 2 → 4,指數遞增不是立即重試

    async def test_backoff_does_not_really_sleep(self) -> None:
        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        backoff = ExponentialBackoff(base=2.0, factor=3.0, max_delay=10.0, sleep=fake_sleep)
        d0 = await backoff.wait()
        d1 = await backoff.wait()
        d2 = await backoff.wait()

        assert d0 == 2.0
        assert d1 == 6.0
        assert d2 == 10.0  # 封頂 max_delay


# ----------------------------------------------------------------------
# 場景 4: 增量更新不重抓已成功的案號
# ----------------------------------------------------------------------
class TestScenarioIncrementalSkip:
    async def test_success_target_is_not_refetched(self, fake_http: FakeHttp) -> None:
        fake_http.default(Resp(status_code=200, text=load_fixture("tender_detail.html")))
        fetcher = PccHttpFetcher(fake_http)
        log = FetchLog()
        target = detail_target("1130108-5", "3.80.11")

        # 第一輪:抓取並記為 SUCCESS
        assert log.should_fetch(target) is True
        await fetcher.fetch_detail("1130108-5", "3.80.11")
        log.record(target, FetchStatus.SUCCESS, http_status=200)
        calls_after_first = len(fake_http.calls)

        # 第二輪半月批次再次涵蓋此案號 → 不應重抓
        if log.should_fetch(target):
            await fetcher.fetch_detail("1130108-5", "3.80.11")

        assert log.should_fetch(target) is False
        assert len(fake_http.calls) == calls_after_first  # 沒有額外 HTTP 呼叫

    async def test_non_success_target_is_refetched(self) -> None:
        log = FetchLog()
        target = detail_target("1130108-5", "3.80.11")

        log.record(target, FetchStatus.FAILED)
        assert log.should_fetch(target) is True

        log.record(target, FetchStatus.BLOCKED)
        assert log.should_fetch(target) is True

        log.record(target, FetchStatus.SUCCESS)
        assert log.should_fetch(target) is False
