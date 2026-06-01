"""任務③ MCP 介面層 — Query BC 讀模型投影 + FastMCP server。"""
from g0vmcp.mcp_server.server import build_mcp
from g0vmcp.mcp_server.service import (
    LifecycleEntryView,
    TenderDetailView,
    TenderQueryService,
    TenderSummaryView,
    VendorAwardView,
)

__all__ = [
    "build_mcp",
    "TenderQueryService",
    "TenderSummaryView",
    "TenderDetailView",
    "LifecycleEntryView",
    "VendorAwardView",
]
