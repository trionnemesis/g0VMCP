# g0VMCP

政府採購標案情報聚合 MCP，用自架擷取與 SQLite 儲存層補完 `pcc-tender` 缺漏欄位。專案目前聚焦衛福部資訊服務類標案，從政府採購網明細頁擷取加值欄位，維護標案生命週期，並透過 FastMCP 對外提供查詢工具。

## 快速開始（團隊安裝）

需要 Python 3.11+。

### 1. 安裝

```bash
# 推薦：uvx（零安裝執行）
uvx g0vmcp

# 或 pipx
pipx install g0vmcp

# 或 pip
pip install g0vmcp
```

### 2. 加入 Claude Code

```bash
claude mcp add g0vmcp -- g0vmcp
```

或手動編輯 `~/.claude/mcp.json`：

```json
{
  "mcpServers": {
    "g0vmcp": {
      "command": "g0vmcp",
      "env": {}
    }
  }
}
```

使用 uvx 時：

```json
{
  "mcpServers": {
    "g0vmcp": {
      "command": "uvx",
      "args": ["g0vmcp"],
      "env": {}
    }
  }
}
```

### 3. 灌入資料

安裝後 DB 為空，需執行同步取得標案資料：

```bash
# 同步招標（近 3 月）+ 決標（近 24 月）
g0vmcp-sync

# 補明細頁 CPC 碼與加值欄位（每批 30 筆，被封鎖自動退避 4h）
g0vmcp-enrich

# 移除非衛福部/非資訊服務類標案（先 dry-run 預覽）
g0vmcp-purge
g0vmcp-purge --apply
```

## 環境變數

| 變數 | 說明 | 預設 |
|------|------|------|
| `G0VMCP_DB` | SQLite DB 路徑 | `~/.g0vmcp/g0vmcp.db` |
| `G0VMCP_TRANSPORT` | MCP transport (`stdio` / `sse`) | `stdio` |
| `G0VMCP_HOST` | SSE transport bind host | `0.0.0.0` |
| `G0VMCP_PORT` | SSE transport port | `8000` |

## MCP tools

### `search_tenders`

以關鍵字、分類、機關、生命週期狀態、日期與金額區間查詢標案清單。

- `state`: `TENDERING`（招標中）/ `AMENDED`（更正）/ `AWARDED`（已決標）/ `FAILED`（無法決標）/ `STALE`（超過 180 天無決標）
- `limit`: 最大 200 筆

### `get_tender_detail`

以 `case_no` 取得標案明細，包含本系統補完的加值欄位（預算、開標時間、截標時間、底價、投標廠商數、標的分類碼）。

### `get_tender_lifecycle`

以 `case_no` 取得公告時間線（招標 → 更正 → 決標）。

### `get_vendor_awards`

以廠商統編查詢得標記錄。

## 功能範圍

- 從 `web.pcc.gov.tw` 明細頁解析預算、開標時間、截標時間、底價、投標廠商家數與標的分類碼。
- 以 `caseNo` 反查 `org_id`，用 `(org_id, job_number)` 作為標案自然鍵。
- 用 domain aggregate 維護招標、更正、決標、無法決標等公告生命週期與不變量。
- 用官方標的分類碼推導 `domain_tag`，缺碼時保留 LLM fallback 與人工複核標記。
- 用 SQLite 儲存 `tenders`、`announcements`、`vendors`、`vendor_awards`。

## 開發

```bash
git clone https://github.com/g0v/g0vmcp.git
cd g0vmcp
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 執行測試
python -m pytest

# 本地開發時指定 DB 路徑
export G0VMCP_DB=./g0vmcp.db
python -m g0vmcp.mcp_server
```

## 專案結構

```text
src/g0vmcp/
  __init__.py               # 版本號
  contracts.py              # 跨層 DTO、Enum、Protocol
  cli.py                    # CLI entry points (sync/enrich/purge)
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

## License

MIT
