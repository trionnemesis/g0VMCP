# 需求覆蓋矩陣 — 驗證 spec 完全覆蓋 B 方案需求

> 需求來源:B 方案架構(自架擷取後端,補完 pcc-tender 實證缺漏的欄位與能力)。
> 目的:確認 `event-storming.md` / `erm.dbml` / `features/*.feature` 無遺漏,再進實作。

| # | 需求(B 方案) | 覆蓋於 spec | 對應任務 | 狀態 |
|---|---|---|---|---|
| R1 | 擷取明細補完整欄位(預算/開標/截標/分類碼/家數/底價) | erm `tenders` 加值欄位 + ingestion.feature「解析加值欄位」 | ① | ✅ |
| R2 | 繞 Cloudflare 計時閘門(自抓 web.pcc.gov.tw,非打 ronny 線上) | event-storming §1 外部系統 + ingestion.feature 背景 + `cf_http` | ① | ✅ |
| R3 | caseNo→org_id 反查 + 換鍵(半月 XML 無 org_id) | erm `tenders.org_id` Note + ingestion.feature「反查並換鍵」 + `repo.rekey` | ① | ✅ |
| R4 | 生命週期串接(招標→更正→決標,含狀態機與不變量) | erm `tenders`+`announcements`+`tender_state` + lifecycle.feature 全 7 scenario | ② | ✅ |
| R5 | 官方 CPC 碼分類(45/84/47→IT,取代標題 ILIKE,解雜訊) | erm `category_*` + classification.feature「CPC 歸 IT/標題誤判」scenario | ② | ✅ |
| R6 | 4 個 MCP tool(search/detail/lifecycle/vendor) | event-storming §5 讀模型 + query.feature 全 7 scenario | ③ | ✅ |
| R7 | pcc-tender 當冷啟動 seed / 交叉對照 | event-storming §1 SEED 虛線 + ingestion.feature | ① | ✅ |
| R8 | 增量更新(半月 OpenData diff,SUCCESS 不重抓) | event-storming §4 政策5 + ingestion.feature「不重抓」 + `fetch_log` | ① | ✅ |
| R9 | 反爬節制(計時閘門退避 retry_after + 中止本批 + 增量) | erm `fetch_log.retry_after`+`fetch_status(BLOCKED)` + ingestion.feature「退避」 | ① | ✅ |
| R10 | 範圍限定:衛福部及轄下機關 ∩ 資訊服務類 | erm Scope 註記 + scope.is_mohw/keyword_prescreen/is_it_cpc + ingestion.feature「baseline 過濾」 | ① | ✅ |
| R11 | 全自動動態抓取(半月 XML baseline,取代硬編碼清單) | opendata.parse_*_xml + pipeline 兩階段 + sync_opendata/enrich_details 腳本 | ① | ✅ |
| R12 | 查詢 scope 護欄(預設聚焦 IT) | service 預設 domain_tag="IT" + query.feature「scope 護欄」scenario | ③ | ✅ |

## 邊界與終局狀態覆蓋(DDD 不變量)

| 不變量 | 覆蓋 scenario |
|---|---|
| (org_id, job_number) 全域唯一 | lifecycle「首見案號時建立標案」 |
| 決標 → AWARDED 終局 | lifecycle「決標公告推進至終局」+「更正不改變終局」 |
| 無法決標 → FAILED 終局 | erm `tender_state.FAILED` + event-storming §2 |
| 公告依日期排序唯一時間線 | lifecycle「公告依日期排序」 |
| 拒絕重複公告 | lifecycle「拒絕重複公告」 |
| 更正不可早於招標 | lifecycle「更正早於招標」 |
| 逾 180 天無決標 → STALE | lifecycle「自動標記 STALE」 |
| 範圍內才落庫(衛福部 ∩ 資訊服務) | ingestion「baseline 過濾」+ classification「CPC 確認/剔除」 |

## 異常流程覆蓋

- baseline 非衛福部 / 黑名單(生醫/清潔)→ 直接略過不落庫(ingestion)
- 反查 org_id 失敗 → 跳過不中斷整批(ingestion)
- Cloudflare 計時閘門擋下 → BLOCKED + retry_after 退避 + 中止本批(ingestion)
- baseline 缺 CPC 碼 → 暫定 IT/llm_fallback,待 enrich 確認(classification)
- 明細頁 CPC 非 IT → 重分類為實際領域,待 purge 剔除(classification)
- 查無資料 → 空集合非例外(query)

---

## ✅ 結論:需求完全覆蓋且已實作

R1–R12 + 7 條不變量 + 6 類異常流程皆落入 spec 並完成實作(112 tests 綠燈)。
範圍由 ingestion 邊界(scope 過濾 + purge)保證,查詢層加 IT 預設護欄。
端到端實證:baseline scope 過濾 + enrich 真實抓取 CPC 碼(842→IT)。
