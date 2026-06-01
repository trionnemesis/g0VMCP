"""啟動進入點:`python -m g0vmcp.mcp_server`(stdio transport)。

DB 路徑解析順序:環境變數 `G0VMCP_DB` > 專案根/g0vmcp.db(絕對路徑)。
Why 絕對路徑:Claude Code 會以任意 cwd spawn 此 server,相對路徑會指向錯誤位置。
"""
from __future__ import annotations

import os
from pathlib import Path

from g0vmcp.mcp_server.server import build_mcp
from g0vmcp.mcp_server.service import TenderQueryService


def _resolve_db_path() -> str:
    env = os.environ.get("G0VMCP_DB")
    if env:
        return env
    # src/g0vmcp/mcp_server/__main__.py → parents[3] = 專案根
    return str(Path(__file__).resolve().parents[3] / "g0vmcp.db")


def main() -> None:
    from g0vmcp.repository import build_repositories

    tender_repo, vendor_repo = build_repositories(_resolve_db_path())
    service = TenderQueryService(tender_repo, vendor_repo)
    mcp = build_mcp(service)
    mcp.run()


if __name__ == "__main__":
    main()
