# g0VMCP

政府採購標案情報聚合 MCP，用自架擷取與 SQLite 儲存層補完 `pcc-tender` 缺漏欄位。專案目前聚焦 B 方案：從政府採購網明細頁擷取加值欄位，維護標案生命週期，並透過 FastMCP 對外提供查詢工具。

## 功能範圍

- 從 `web.pcc.gov.tw` 明細頁解析預算、開標時間、截標時間、底價、投標廠商家數與標的分類碼。
- 以 `caseNo` 反查 `org_id`，用 `(org_id, job_number)` 作為標案自然鍵。
- 用 domain aggregate 維護招標、更正、決標、無法決標等公告生命週期與不變量。
- 用官方標的分類碼推導 `domain_tag`，缺碼時保留 LLM fallback 與人工複核標記。
- 用 SQLite 儲存 `tenders`、`announcements`、`vendors`、`vendor_awards`。
- 提供 4 個 MCP tools：`search_tenders`、`get_tender_detail`、`get_tender_lifecycle`、`get_vendor_awards`。

## 專案結構

```text
src/g0vmcp/
  contracts.py              # 跨層 DTO、Enum、Protocol
  domain/                   # 標案生命週期、分類與領域事件
  ingestion/                # PCC HTTP 擷取與 HTML 解析
  repository/               # SQLite schema 與 repository implementation
  mcp_server/               # FastMCP tools 與查詢 service
spec/
  features/                 # Gherkin 行為規格
  erm.dbml                  # 資料模型
  event-storming.md         # 事件風暴與流程設計
tests/                      # domain、ingestion、repository、mcp、integration tests
```

## 安裝

需要 Python 3.11 以上。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## 執行測試

```bash
python -m pytest
```

測試涵蓋：

- 標案生命週期不變量。
- 官方分類碼與 LLM fallback 分類。
- PCC 明細頁解析與反爬阻擋處理。
- SQLite repository 查詢與投影。
- MCP service 與 tool wiring。
- repository 到 service 的端到端整合。

## 啟動 MCP server

安裝套件後可用 module entrypoint 啟動：

```bash
python -m g0vmcp.mcp_server
```

預設會建立或使用目前目錄下的 `g0vmcp.db` SQLite database。啟動時會透過 `g0vmcp.repository.build_repositories()` 初始化 schema，並把 repository 注入 `TenderQueryService` 與 FastMCP server。

## MCP tools

### `search_tenders`

以關鍵字、分類、機關、日期與金額區間查詢標案清單。

### `get_tender_detail`

以 `case_no` 取得標案明細，包含本系統補完的加值欄位。

### `get_tender_lifecycle`

以 `case_no` 取得公告時間線。

### `get_vendor_awards`

以廠商統編查詢得標記錄。

## Git

此 repository 已初始化 git。建議不要提交本機產物，例如 `.venv/`、`__pycache__/`、`.pytest_cache/` 與 SQLite database 檔案。
