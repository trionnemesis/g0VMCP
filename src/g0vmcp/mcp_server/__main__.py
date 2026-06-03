"""啟動進入點:`g0vmcp` 或 `python -m g0vmcp.mcp_server`。

DB 路徑解析順序:
  1. 環境變數 G0VMCP_DB
  2. 預設 ~/.g0vmcp/g0vmcp.db（自動建目錄;pip install 後可用）
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
    data_dir = Path.home() / ".g0vmcp"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "g0vmcp.db")


def main() -> None:
    from g0vmcp.repository import build_repositories

    db_path = _resolve_db_path()
    tender_repo, vendor_repo = build_repositories(db_path)
    service = TenderQueryService(tender_repo, vendor_repo)
    mcp = build_mcp(service)

    transport = os.environ.get("G0VMCP_TRANSPORT", "stdio")
    if transport == "sse":
        host = os.environ.get("G0VMCP_HOST", "0.0.0.0")
        port = int(os.environ.get("G0VMCP_PORT", "8000"))
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
