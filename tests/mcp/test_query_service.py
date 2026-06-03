"""Service 層端到端測試 — 對應 tender-query.feature 5 scenario。"""
from __future__ import annotations

from datetime import datetime

import pytest

from g0vmcp.mcp_server.service import TenderQueryService


# 場景: search_tenders 以分類與金額區間過濾
async def test_search_tenders_filters_by_domain_and_budget(
    service: TenderQueryService,
) -> None:
    results = await service.search_tenders(domain_tag="IT", budget_min=1_000_000)

    # 只剩 IT 且 budget >= 1,000,000 的那筆(小案 500k 與工程案被濾掉)
    assert len(results) == 1
    hit = results[0]
    assert hit.domain_tag == "IT"
    assert hit.budget is not None and hit.budget >= 1_000_000
    assert hit.case_no == "3.79:1130108-5"


# 場景: search_tenders 未指定 domain_tag 時預設只回 IT(本 MCP 資料範圍護欄)
async def test_search_tenders_defaults_to_it_domain(
    service: TenderQueryService,
) -> None:
    # 不傳 domain_tag → 預設帶入 IT,工程案(construction)應被排除
    results = await service.search_tenders()

    assert len(results) == 2  # it_big + it_small,工程案被預設護欄濾掉
    assert all(r.domain_tag == "IT" for r in results)


# 場景: 明確傳其他 domain_tag 時仍尊重(以利除錯)
async def test_search_tenders_explicit_domain_tag_overrides_default(
    service: TenderQueryService,
) -> None:
    results = await service.search_tenders(domain_tag="工程")

    assert len(results) == 1
    assert results[0].domain_tag == "工程"


# 場景: search_tenders 以生命週期狀態過濾(找尚未決標的標案)
async def test_search_tenders_filters_by_state(
    service: TenderQueryService,
) -> None:
    # 預設 IT 護欄 + state=TENDERING → 僅 it_small(工程案非 IT 被濾掉)
    results = await service.search_tenders(state="TENDERING")

    assert len(results) == 1
    assert all(r.state == "TENDERING" for r in results)
    assert all(r.domain_tag == "IT" for r in results)


# 場景: search_tenders 傳入非法狀態值應拒絕
async def test_search_tenders_rejects_invalid_state(
    service: TenderQueryService,
) -> None:
    with pytest.raises(ValueError, match="invalid state"):
        await service.search_tenders(state="NOT_A_STATE")


# 場景: get_tender_detail 回傳加值欄位
async def test_get_tender_detail_includes_value_added_fields(
    service: TenderQueryService,
) -> None:
    detail = await service.get_tender_detail("3.79:1130108-5")

    assert detail is not None
    # pcc-tender 缺的加值欄位皆有值
    assert detail.budget == 5_000_000
    assert detail.open_date == datetime(2024, 1, 20, 9, 30)
    assert detail.bid_deadline == datetime(2024, 1, 19, 17, 0)
    assert detail.category_code == "3399"


# 場景: get_tender_lifecycle 回傳事件時間線
async def test_get_tender_lifecycle_returns_ordered_timeline(
    service: TenderQueryService,
) -> None:
    timeline = await service.get_tender_lifecycle("3.79:1130108-5")

    # 依日期排序:招標(1/8) → 決標(3/15)
    assert [e.ann_type for e in timeline] == ["招標公告", "決標公告"]
    assert timeline[0].ann_date < timeline[1].ann_date


# 場景: get_vendor_awards 以統編查得標記錄
async def test_get_vendor_awards_returns_all_records(
    service: TenderQueryService,
) -> None:
    awards = await service.get_vendor_awards("12345678")

    assert len(awards) == 3
    assert {a.award_price for a in awards} == {4_800_000, 1_200_000, 2_000_000}
    assert all(a.currency == "TWD" for a in awards)


# 場景: 查無資料時回傳空集合而非錯誤
async def test_get_tender_detail_returns_none_when_not_found(
    service: TenderQueryService,
) -> None:
    detail = await service.get_tender_detail("0000000")
    assert detail is None


async def test_lifecycle_and_awards_empty_when_not_found(
    service: TenderQueryService,
) -> None:
    assert await service.get_tender_lifecycle("0000000") == []
    assert await service.get_vendor_awards("99999999") == []
