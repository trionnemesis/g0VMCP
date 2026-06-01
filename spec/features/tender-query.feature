# language: zh-TW
# 對應任務③ MCP 介面層 / Query BC
功能: MCP 查詢介面
  作為 MCP client(LLM)
  我想要 透過 4 個 tool 查詢標案情報
  以便 取得比 pcc-tender 更完整的標案資料

  場景: search_tenders 以分類與金額區間過濾
    假設 已有數筆已分類標案
    當 我呼叫 search_tenders(domain_tag="IT", budget_min=1000000)
    那麼 應只回傳 domain_tag 為 "IT" 且 budget >= 1000000 的標案

  場景: get_tender_detail 回傳加值欄位
    假設 案號 "1130108-5" 已擷取明細
    當 我呼叫 get_tender_detail("1130108-5")
    那麼 結果應包含 budget、open_date、bid_deadline、category_code
    # 這些正是 pcc-tender 沒有的欄位

  場景: get_tender_lifecycle 回傳事件時間線
    假設 案號 "1130108-5" 有招標與決標兩筆公告
    當 我呼叫 get_tender_lifecycle("1130108-5")
    那麼 應回傳依序排列的 [招標公告, 決標公告] 時間線

  場景: get_vendor_awards 以統編查得標記錄
    假設 廠商統編 "12345678" 有 3 筆得標記錄
    當 我呼叫 get_vendor_awards("12345678")
    那麼 應回傳該廠商的 3 筆得標標案與決標金額

  場景: 查無資料時回傳空集合而非錯誤
    假設 案號 "0000000" 不存在
    當 我呼叫 get_tender_detail("0000000")
    那麼 應回傳空結果(非拋出例外)
