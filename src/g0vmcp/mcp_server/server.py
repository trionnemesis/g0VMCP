"""FastMCP server — 4 個查詢 tool。

Why 分離: tool body 只做轉接(parse args → 呼叫 service → 回投影 DTO),
業務邏輯全在 TenderQueryService。build_mcp() 接受 service 以利測試注入 fake repo。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastmcp import FastMCP

from g0vmcp import __version__
from g0vmcp.mcp_server.service import (
    LifecycleEntryView,
    TenderDetailView,
    TenderQueryService,
    TenderSummaryView,
    VendorAwardView,
)


def build_mcp(service: TenderQueryService) -> FastMCP:
    mcp: FastMCP = FastMCP(
        "g0vmcp",
        version=__version__,
        instructions=(
            "政府採購標案情報 MCP — 查詢衛福部資訊服務類標案。"
            "提供搜尋、明細、生命週期時間線、廠商得標記錄等工具。"
            "資料來源：政府電子採購網 (web.pcc.gov.tw)。"
        ),
    )

    @mcp.tool
    async def search_tenders(
        keyword: Optional[str] = None,
        domain_tag: Optional[str] = None,
        agency: Optional[str] = None,
        state: Optional[str] = None,
        budget_min: Optional[int] = None,
        budget_max: Optional[int] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        limit: int = 50,
    ) -> list[TenderSummaryView]:
        """以關鍵字、分類、機關、生命週期狀態、日期、金額區間查詢標案。

        本 MCP 資料範圍 = 衛生福利部及轄下機關的「資訊服務類」標案。
        domain_tag 預設 IT:未指定時只回資訊服務類;傳其他值(除錯用)仍尊重。

        state: 生命週期狀態,擇一 TENDERING(招標中/尚未決標)/AMENDED(更正)/
            AWARDED(已決標)/FAILED(無法決標)/STALE(超過180天無決標)。
            查「尚未決標」傳 state="TENDERING"。
        """
        return await service.search_tenders(
            keyword=keyword,
            domain_tag=domain_tag,
            agency=agency,
            state=state,
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
