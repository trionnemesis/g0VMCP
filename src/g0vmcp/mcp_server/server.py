"""FastMCP server — 4 個查詢 tool。

Why 分離: tool body 只做轉接(parse args → 呼叫 service → 回投影 DTO),
業務邏輯全在 TenderQueryService。build_mcp() 接受 service 以利測試注入 fake repo。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastmcp import FastMCP

from g0vmcp.mcp_server.service import (
    LifecycleEntryView,
    TenderDetailView,
    TenderQueryService,
    TenderSummaryView,
    VendorAwardView,
)


def build_mcp(service: TenderQueryService) -> FastMCP:
    mcp: FastMCP = FastMCP("g0vmcp")

    @mcp.tool
    async def search_tenders(
        keyword: Optional[str] = None,
        domain_tag: Optional[str] = None,
        agency: Optional[str] = None,
        budget_min: Optional[int] = None,
        budget_max: Optional[int] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        limit: int = 50,
    ) -> list[TenderSummaryView]:
        """以關鍵字、分類、機關、日期、金額區間查詢標案。"""
        return await service.search_tenders(
            keyword=keyword,
            domain_tag=domain_tag,
            agency=agency,
            budget_min=budget_min,
            budget_max=budget_max,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    @mcp.tool
    async def get_tender_detail(case_no: str) -> Optional[TenderDetailView]:
        """取得標案完整明細(含 pcc-tender 缺的加值欄位)。查無回 null。"""
        return await service.get_tender_detail(case_no)

    @mcp.tool
    async def get_tender_lifecycle(case_no: str) -> list[LifecycleEntryView]:
        """取得標案公告事件時間線(招標→更正→決標)。"""
        return await service.get_tender_lifecycle(case_no)

    @mcp.tool
    async def get_vendor_awards(tax_id: str) -> list[VendorAwardView]:
        """以統編查廠商得標記錄。"""
        return await service.get_vendor_awards(tax_id)

    return mcp
