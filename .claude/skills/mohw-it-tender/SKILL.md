---
name: mohw-it-tender
description: >
  Query government procurement tenders for the Ministry of Health and Welfare (MOHW) IT services.
  Uses twinkle-hub pcc-tender dataset as the data source.
  Covers: searching tenders, filtering IT services, award analysis, vendor lookup, lifecycle tracking.
---

# MOHW IT Tender Intelligence

Query 衛福部及轄下機關的資訊服務類標案。資料來源：twinkle-hub `pcc-tender` dataset（政府電子採購網半月公開資料，162k+ rows，2015-04 → now）。

## When to Use

- User asks about 衛福部 / 衛生福利部 procurement, tenders, or contracts
- User asks about government IT procurement for health agencies
- Keywords: 標案, 招標, 決標, 採購, 得標, 廠商, 資訊服務

## Data Source

Use `query_rows("pcc-tender", ...)` from twinkle-hub MCP. Do NOT use g0vmcp MCP tools for basic searches -- pcc-tender has broader coverage (162k nationwide vs g0vmcp's ~34 衛福部 IT records).

### Available Columns

| Column | Type | Description | Example |
|--------|------|-------------|---------|
| `date` | date | 公告日期 | 2026-05-15 |
| `announcement_type` | text | 招標公告 / 決標公告 | 決標公告 |
| `title` | text | 標案名稱 | 衛生福利部資訊系統維運案 |
| `agency` | text | 機關名稱 | 衛生福利部中央健康保險署 |
| `agency_id` | text | 機關代碼（often empty） | |
| `job_number` | text | 標案案號（unique key） | 1130201 |
| `companies` | text | 得標廠商（text, 非統一編號） | 中華電信股份有限公司 |
| `procurement_type` | text | 招標方式 | 公開招標 / 限制性招標 |
| `procurement_attr` | text | 採購性質 | 勞務類 / 財物類 / 工程類 |
| `award_way` | text | 決標方式 | 最低標 / 最有利標 / 準用最有利標 |
| `award_price` | text | 決標金額（string, cast to BIGINT for SUM） | 15000000 |
| `notice_date` | date | 刊登日期 | 2026-05-16 |
| `contact_phone` | text | 聯絡電話 | (02)85906666 |
| `contact_person` | text | 聯絡人 | 王小明 |
| `agency_addr` | text | 機關地址 | 100台北市中正區... |
| `bidder_addr` | text | 廠商地址 | 100台北市信義區... |

### Columns NOT Available (enrichment gap)

These fields require scraping PCC detail pages; pcc-tender does not include them:

- **budget (預算金額)** -- different from award_price; not available
- **open_date (開標時間)** -- not available
- **bid_deadline (截止投標日)** -- not available
- **base_price (底價)** -- not available (usually confidential anyway)
- **bidder_count (投標廠商家數)** -- not available
- **category_code (CPC 標的分類碼)** -- not available; IT filtering uses keyword heuristics

When user asks for these fields, clearly state they are not in the dataset and offer to attempt on-demand enrichment (see section below).

---

## Domain Knowledge

### MOHW Agency Filtering

All 衛福部轄下機關 share the prefix `衛生福利部`:

```sql
agency ILIKE '%衛生福利部%'
```

This matches: 衛生福利部, 衛生福利部中央健康保險署, 衛生福利部疾病管制署, 衛生福利部國民健康署, 衛生福利部食品藥物管理署, 衛生福利部社會及家庭署, 各附設醫院 (桃園, 臺中, 臺南, etc.)

### IT Services Identification

pcc-tender has no CPC classification code. Use keyword heuristics on `title`:

**Whitelist** (include if title contains ANY):
資訊, 資安, 系統, 軟體, 硬體, 網站, 入口網, 網路, 平臺, 平台, 雲端, 機房, 伺服器, 主機, 數位, 電子化, APP, API, 資料庫, 委外, 維運, 資通, 電信

**Blacklist** (exclude if title contains ANY, takes priority over whitelist):
PCR, 核酸, 蛋白質, 定序, 基因, 微生物, 試劑, 耗材, 疫苗, 藥品, 清潔, 培養

**SQL pattern for IT filter**:
```sql
AND (
  title ILIKE '%資訊%' OR title ILIKE '%資安%' OR title ILIKE '%系統%'
  OR title ILIKE '%軟體%' OR title ILIKE '%硬體%' OR title ILIKE '%網站%'
  OR title ILIKE '%網路%' OR title ILIKE '%平臺%' OR title ILIKE '%平台%'
  OR title ILIKE '%雲端%' OR title ILIKE '%伺服器%' OR title ILIKE '%維運%'
  OR title ILIKE '%資通%' OR title ILIKE '%數位%' OR title ILIKE '%電子化%'
)
AND title NOT ILIKE '%試劑%'
AND title NOT ILIKE '%疫苗%'
AND title NOT ILIKE '%清潔%'
AND title NOT ILIKE '%藥品%'
AND title NOT ILIKE '%耗材%'
```

Note: This is heuristic-based. False positives are possible (e.g., "資訊大樓清潔勞務" matches 資訊 but blacklisted by 清潔). Always present results as "candidate IT tenders" rather than "confirmed IT tenders."

### CPC Code Reference (for interpreting detail pages)

If you fetch a detail page and see a CPC code, use this mapping:

| CPC Prefix | Domain | Description |
|------------|--------|-------------|
| 45 | IT | 計算機及週邊設備 |
| 84 | IT | 電腦及相關服務 |
| 47 | IT | 通訊器材 |
| 41 | 工程 | 土木建築工程 |
| 42 | 工程 | 機電工程 |
| 51 | 醫療 | 醫療器材/藥品 |
| 52 | 醫療 | 醫療服務 |
| 71 | 清潔 | 清潔服務 |
| 72 | 清潔 | 環境維護 |

---

## Query Templates

### 1. Search MOHW IT Tenders (basic)

```
query_rows("pcc-tender",
  where="agency ILIKE '%衛生福利部%' AND date >= '2026-01-01' AND (title ILIKE '%資訊%' OR title ILIKE '%系統%' OR title ILIKE '%軟體%' OR title ILIKE '%維運%')",
  limit=50)
```

### 2. Search by Specific Agency

```
query_rows("pcc-tender",
  where="agency ILIKE '%衛生福利部中央健康保險署%' AND date >= '2025-01-01'",
  limit=50)
```

### 3. Award Analysis (aggregate by agency)

```
query_rows("pcc-tender",
  where="agency ILIKE '%衛生福利部%' AND announcement_type='決標公告' AND date >= '2025-01-01' AND (title ILIKE '%資訊%' OR title ILIKE '%系統%')",
  columns=["agency", "COUNT(*) AS tender_count", "SUM(CAST(award_price AS BIGINT)) AS total_award"],
  group_by=["agency"],
  order_by="total_award DESC",
  limit=20)
```

### 4. Vendor Lookup (by company name)

```
query_rows("pcc-tender",
  where="announcement_type='決標公告' AND companies ILIKE '%中華電信%' AND agency ILIKE '%衛生福利部%'",
  columns=["date", "title", "agency", "award_price", "companies"],
  order_by="date DESC",
  limit=30)
```

### 5. Lifecycle Tracking (tender → award progression)

To see if a tender has been awarded, query both announcement types for the same job_number:

```
query_rows("pcc-tender",
  where="job_number = '1130201'",
  columns=["date", "announcement_type", "title", "agency", "award_price", "companies", "procurement_type", "award_way"],
  order_by="date ASC")
```

To find tenders that may not yet be awarded, first query 招標公告, then check each job_number for a 決標公告 entry:

```
-- Step 1: Get recent 招標公告
query_rows("pcc-tender",
  where="agency ILIKE '%衛生福利部%' AND announcement_type='招標公告' AND date >= '2026-01-01' AND (title ILIKE '%資訊%' OR title ILIKE '%系統%')",
  columns=["job_number", "date", "title", "agency", "procurement_type"],
  order_by="date DESC",
  limit=50)

-- Step 2: For each interesting job_number, check lifecycle
query_rows("pcc-tender",
  where="job_number = '{job_number}'",
  columns=["date", "announcement_type", "award_price", "companies", "award_way"],
  order_by="date ASC")
-- If only 招標公告 rows → still tendering; if 決標公告 exists → awarded
```

### 6. Procurement Method Analysis

```
query_rows("pcc-tender",
  where="agency ILIKE '%衛生福利部%' AND announcement_type='決標公告' AND date >= '2025-01-01'",
  columns=["procurement_type", "COUNT(*) AS count", "SUM(CAST(award_price AS BIGINT)) AS total"],
  group_by=["procurement_type"],
  order_by="total DESC")
```

---

## On-Demand Enrichment

When the user needs fields not in pcc-tender (budget, open_date, bid_deadline):

### Option 1: Direct Browser Link (recommended)

Provide the user a clickable PCC search URL:

```
https://web.pcc.gov.tw/prkms/tender/common/basic/readTenderBasic?caseNo={job_number}
```

The user can open it in a browser to see the full detail page with budget, open_date, CPC code, etc.

### Option 2: Browser Automation (if Claude in Chrome available)

If the user has Claude in Chrome connected, navigate to the PCC search URL and extract fields from the rendered page. Look for:
- **預算金額** → budget
- **開標時間** → open_date (ROC date format: `115/06/03 14:30`)
- **截止投標日期** → bid_deadline
- **標的分類** → category code (e.g., `4523 - 資訊處理及週邊設備`)

### ROC Date Conversion

ROC year + 1911 = Western year. Example: `115/06/03` → `2026/06/03`

### Why NOT fetch_url_as_markdown

PCC detail pages are POST-based dynamic forms with JavaScript rendering. `fetch_url_as_markdown` (trafilatura) cannot extract structured data from these pages. Do not attempt this approach.

### Option 3: g0VMCP MCP (if installed)

If the user has g0VMCP MCP server configured, use `get_tender_detail(case_no)` for enriched fields. This is the batch-analytics path — see the g0VMCP README for setup.

---

## Response Guidelines

1. **Always show results as a table** with key columns: date, title, agency, procurement_type, award_price
2. **Clearly label data limitations** when budget/open_date are requested but unavailable
3. **Use award_price (決標金額)** when user asks for 金額; clarify this is the awarded amount, not the budget
4. **IT classification caveat**: mention results are keyword-filtered and may include false positives
5. **ROC dates**: convert to Western calendar for display
6. **Large numbers**: format with commas (e.g., 15,000,000) and optionally show in 萬 units for readability
