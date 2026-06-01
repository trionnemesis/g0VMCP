# language: zh-TW
# 對應任務② 領域+儲存層 / Classification BC
功能: 標案分類
  作為 資料平台
  我想要 用官方標的分類碼決定領域標籤
  以便 取代標題關鍵字 ILIKE,消除 IT 標案的分類雜訊

  場景: 以官方分類碼歸入 IT 領域
    假設 標案的 category_code 對應「資訊服務/設備」
    當 執行分類
    那麼 應產生 TenderClassified 事件
    而且 domain_tag 應為 "IT"
    而且 分類方法 method 應為 "official_code"

  場景: 標題含 IT 字眼但分類碼非 IT — 不誤判
    假設 標案標題為 "資訊大樓清潔勞務" 但 category_code 屬「清潔服務」
    當 執行分類
    那麼 domain_tag 不應為 "IT"
    # 這正是標題 ILIKE 會誤判、官方分類碼能避免的案例

  場景: 缺分類碼時退回 LLM 邊界分類
    假設 標案沒有 category_code
    當 執行分類
    那麼 method 應為 "llm_fallback"
    而且 應標記為需人工複核
