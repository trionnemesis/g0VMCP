"""MCP tool 註冊與呼叫測試 — 用 fastmcp in-memory Client 端到端驗證轉接層。"""
from __future__ import annotations

import pytest
from fastmcp import Client

from g0vmcp.mcp_server.server import build_mcp
from g0vmcp.mcp_server.service import TenderQueryService


@pytest.fixture
def mcp(service: TenderQueryService):
    return build_mcp(service)


async def test_four_tools_registered(mcp) -> None:
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == {
        "search_tenders",
        "get_tender_detail",
        "get_tender_lifecycle",
        "get_vendor_awards",
    }


# 場景: search_tenders 以分類與金額區間過濾(經 MCP tool)
async def test_search_tenders_tool(mcp) -> None:
    async with Client(mcp) as client:
        res = await client.call_tool(
            "search_tenders", {"domain_tag": "IT", "budget_min": 1_000_000}
        )
    assert len(res.data) == 1
    assert res.data[0].case_no == "3.79:1130108-5"


# 場景: search_tenders 以狀態過濾找尚未決標(經 MCP tool)
async def test_search_tenders_by_state_tool(mcp) -> None:
    # 預設 IT 護欄 + state=TENDERING → 僅 it_small(工程案非 IT 被濾掉)
    async with Client(mcp) as client:
        res = await client.call_tool("search_tenders", {"state": "TENDERING"})
    assert len(res.data) == 1
    assert all(r.state == "TENDERING" for r in res.data)
    assert all(r.domain_tag == "IT" for r in res.data)


# 場景: 不傳 domain_tag 時預設只回 IT(資料範圍護欄,經 MCP tool)
async def test_search_tenders_defaults_to_it_tool(mcp) -> None:
    async with Client(mcp) as client:
        res = await client.call_tool("search_tenders", {})
    assert len(res.data) == 2
    assert all(r.domain_tag == "IT" for r in res.data)


# 場景: get_tender_detail 回傳加值欄位(經 MCP tool)
async def test_get_tender_detail_tool(mcp) -> None:
    async with Client(mcp) as client:
        res = await client.call_tool(
            "get_tender_detail", {"case_no": "3.79:1130108-5"}
        )
    assert res.data.budget == 5_000_000
    assert res.data.category_code == "3399"
    assert res.data.open_date is not None
    assert res.data.bid_deadline is not None


# 場景: get_tender_lifecycle 回傳事件時間線(經 MCP tool)
async def test_get_tender_lifecycle_tool(mcp) -> None:
    async with Client(mcp) as client:
        res = await client.call_tool(
            "get_tender_lifecycle", {"case_no": "3.79:1130108-5"}
        )
    assert [e.ann_type for e in res.data] == ["招標公告", "決標公告"]


# 場景: get_vendor_awards 以統編查得標記錄(經 MCP tool)
async def test_get_vendor_awards_tool(mcp) -> None:
    async with Client(mcp) as client:
        res = await client.call_tool("get_vendor_awards", {"tax_id": "12345678"})
    assert len(res.data) == 3


# 場景: 查無資料時回傳空結果而非錯誤(經 MCP tool)
async def test_get_tender_detail_tool_not_found(mcp) -> None:
    async with Client(mcp) as client:
        res = await client.call_tool("get_tender_detail", {"case_no": "0000000"})
    assert res.data is None
