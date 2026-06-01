# language: zh-TW
# 對應任務① 擷取層 / Ingestion BC
功能: 標案明細擷取
  作為 資料平台
  我想要 從政府電子採購網明細頁抓取完整欄位
  以便 補齊 pcc-tender 摘要層缺漏的 預算/開標/分類碼 等欄位

  背景:
    假設 擷取來源是 web.pcc.gov.tw 明細頁(非 ronny 線上服務)
    而且 擷取需遵守 rate-limit 與增量原則

  場景: 從明細頁解析出 pcc-tender 缺漏的加值欄位
    假設 一筆招標公告的案號為 "1130108-5"
    而且 其機關代碼 org_id 為 "3.80.11"
    當 我擷取該案號的明細頁
    那麼 我應該得到 TenderDetailParsed 事件
    而且 payload 應包含 budget(預算金額)
    而且 payload 應包含 open_date(開標時間)
    而且 payload 應包含 category_code(標的分類碼)

  場景: 案號缺機關代碼時須先反查 org_id
    假設 一筆來自 pcc-tender 的公告只有 job_number 沒有 org_id
    當 我準備擷取其明細頁
    那麼 系統應先以 job_number + 機關名稱反查出 org_id
    而且 反查失敗時該筆標記為 FAILED 並跳過,不可中斷整批

  場景: 被反爬機制擋下時退避而非重試風暴
    假設 明細頁回應為 Cloudflare 阻擋(HTTP 403/429)
    當 擷取發生
    那麼 該 target 的 fetch_status 應為 BLOCKED
    而且 系統應指數退避,不可立即高頻重試

  場景: 增量更新不重抓已成功的案號
    假設 案號 "1130108-5" 的 fetch_log 已為 SUCCESS
    當 新一輪半月批次再次涵蓋此案號
    那麼 系統不應重新擷取該明細頁
