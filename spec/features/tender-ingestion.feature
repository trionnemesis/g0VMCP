# language: zh-TW
# 對應任務① 擷取層 / Ingestion BC
功能: 標案明細擷取
  作為 資料平台
  我想要 動態下載半月公開資料並補齊明細頁欄位
  以便 自動聚合「衛福部 ∩ 資訊服務類」標案,取代硬編碼清單

  背景:
    假設 資料源是 web.pcc.gov.tw 半月公開資料 XML(downloadFile?fileName=tender_*/award_*)
    而且 採兩階段: ① 半月 XML baseline 落庫 → ② 明細頁補 CPC 碼與加值欄位
    而且 擷取需遵守 rate-limit 與增量原則

  場景: 階段一 baseline 只收衛福部標案,剔除生醫/清潔等黑名單
    假設 一份半月招標 XML 含多筆全國標案
    當 我做 baseline 落庫
    那麼 機關名稱非以「衛生福利部」開頭者應被略過
    而且 標題含黑名單字(PCR/核酸/疫苗/試劑/清潔…)者應被略過
    而且 通過者以暫定分類 domain_tag="IT"、method="llm_fallback" 落庫
    而且 其 fetch_log 狀態為 PENDING(待階段二補 CPC 碼)

  場景: 階段二從明細頁解析出 pcc-tender 缺漏的加值欄位
    假設 一筆招標公告的案號為 "1130108-5"
    而且 其機關代碼 org_id 為 "3.80.11"
    當 我擷取該案號的明細頁
    那麼 我應該得到 TenderDetailParsed 事件
    而且 payload 應包含 budget(預算金額)
    而且 payload 應包含 open_date(開標時間)
    而且 payload 應包含 category_code(標的分類碼)

  場景: baseline 無 org_id,enrich 經搜尋頁反查並換鍵
    假設 baseline 落庫的標案只有 job_number、org_id 為空
    當 我做明細 enrich(readTenderBasic POST 搜尋 → tpam 明細頁)
    那麼 系統應從搜尋結果反查出 org_id
    而且 tender_id 由 ":caseNo" 換鍵為 "orgId:caseNo"(先搬舊列再寫新鍵)
    而且 反查失敗時該筆標記為 FAILED 並跳過,不可中斷整批

  場景: 被反爬計時閘門擋下時退避而非重試風暴
    假設 明細頁回應為 Cloudflare 計時閘門且重試耗盡
    當 擷取發生
    那麼 該 target 的 fetch_status 應為 BLOCKED
    而且 應設 retry_after(預設 +4h),退避期內 should_fetch 為否
    而且 應中止本批,避免持續觸發封鎖

  場景: 增量更新不重抓已成功的案號
    假設 案號 "1130108-5" 的 fetch_log 已為 SUCCESS
    當 新一輪半月批次再次涵蓋此案號
    那麼 系統不應重新擷取該明細頁
