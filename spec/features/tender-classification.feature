# language: zh-TW
# 對應任務② 領域+儲存層 / Classification BC
功能: 標案分類
  作為 資料平台
  我想要 用官方標的分類 CPC 碼決定領域標籤
  以便 取代標題關鍵字 ILIKE,精確判定資訊服務類、消除分類雜訊

  # 資訊服務類 CPC 碼前綴: 45(計算機) / 84(電腦服務,如 842 軟體) / 47(通訊)
  場景大綱: 以官方 CPC 碼歸入 IT 領域
    假設 標案的 category_code 為 "<code>"
    當 執行分類
    那麼 應產生 TenderClassified 事件
    而且 domain_tag 應為 "IT"
    而且 分類方法 method 應為 "official_code"

    例子:
      | code |
      | 4523 |
      | 842  |
      | 4712 |

  場景: 標題含 IT 字眼但 CPC 碼非 IT — 不誤判
    假設 標案標題為 "醫療影像資訊系統建置案" 但 category_code 為 "5159"(醫療)
    當 執行分類
    那麼 domain_tag 不應為 "IT"
    # 關鍵字初篩會暫收為 IT,明細頁 CPC 碼確認後重分類剔除(待 purge)

  場景: baseline 缺 CPC 碼時暫定 IT 待確認
    假設 半月 XML baseline 的標案通過關鍵字初篩但尚無 category_code
    當 執行分類
    那麼 method 應為 "llm_fallback"
    而且 domain_tag 暫定為 "IT" 並標記為需明細頁 CPC 碼確認
