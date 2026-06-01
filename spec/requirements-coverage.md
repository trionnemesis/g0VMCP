# 需求覆蓋矩陣 — 驗證 spec 完全覆蓋 B 方案需求

> 需求來源:B 方案架構(自架擷取後端,補完 pcc-tender 實證缺漏的欄位與能力)。
> 目的:確認 `event-storming.md` / `erm.dbml` / `features/*.feature` 無遺漏,再進實作。

| # | 需求(B 方案) | 覆蓋於 spec | 對應任務 | 狀態 |
|---|---|---|---|---|
| R1 | 擷取明細補完整欄位(預算/開標/截標/分類碼/家數/底價) | erm `tenders` 加值欄位 + ingestion.feature「解析加值欄位」 | ① | ✅ |
| R2 | 繞 Cloudflare(自抓 web.pcc.gov.tw,非打 ronny 線上) | event-storming §1 外部系統 + ingestion.feature 背景 | ① | ✅ |
| R3 | caseNo→org_id 反查(因 pcc-tender agency_id 全空) | erm `tenders.org_id` Note + ingestion.feature「反查 org_id」 | ① | ✅ |
| R4 | 生命週期串接(招標→更正→決標,含狀態機與不變量) | erm `tenders`+`announcements`+`tender_state` + lifecycle.feature 全 6 scenario | ② | ✅ |
| R5 | 官方標的分類碼分類(取代標題 ILIKE,解 IT 雜訊) | erm `category_code/domain_tag` + classification.feature「標題誤判」scenario | ② | ✅ |
| R6 | 4 個 MCP tool(search/detail/lifecycle/vendor) | event-storming §5 讀模型 + query.feature 全 5 scenario | ③ | ✅ |
| R7 | pcc-tender 當冷啟動 seed / 交叉對照 | event-storming §1 SEED 虛線 + ingestion.feature「反查 org_id」 | ① | ✅ |
| R8 | 增量更新(半月 OpenData diff) | event-storming §4 政策5 + ingestion.feature「不重抓」 | ① | ✅ |
| R9 | 反爬節制(rate-limit + 退避 + 增量) | erm `fetch_log`+`fetch_status(BLOCKED)` + ingestion.feature「退避」 | ① | ✅ |

## 邊界與終局狀態覆蓋(DDD 不變量)

| 不變量 | 覆蓋 scenario |
|---|---|
| (org_id, job_number) 全域唯一 | lifecycle「首見案號時建立標案」 |
| 決標 → AWARDED 終局 | lifecycle「決標公告推進至終局」+「更正不改變終局」 |
| 無法決標 → FAILED 終局 | erm `tender_state.FAILED` + event-storming §2 |
| 公告依日期排序唯一時間線 | lifecycle「公告依日期排序」 |
| 拒絕重複公告 | lifecycle「拒絕重複公告」 |
| 更正不可早於招標 | lifecycle「更正早於招標」 |

## 異常流程覆蓋

- 反查 org_id 失敗 → 跳過不中斷整批(ingestion)
- Cloudflare 阻擋 → BLOCKED + 退避(ingestion)
- 缺分類碼 → LLM fallback + 人工複核(classification)
- 查無資料 → 空集合非例外(query)

---

## ✅ 結論:需求完全覆蓋

R1–R9 + 6 條不變量 + 4 類異常流程皆已落入 spec。三任務契約(domain shape、tool 簽名、行為驗收)由 spec 鎖定,**可進入平行實作**。
