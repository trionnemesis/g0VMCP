"""出站 URL 護欄 — 把 PCC 抓取限制在 https://web.pcc.gov.tw。

兩個獨立的風險,兩個函式:

`assert_pcc_url` 擋 scheme/host。urllib 的 `build_opener()` 預設帶 FileHandler /
FTPHandler / DataHandler,所以任何走到 opener 的非 http(s) URL 都會被實際開啟 ——
`file:///etc/passwd` 會被讀回來當成 HTML(CWE-22 / CWE-918)。目前所有呼叫端都是
以 `_BASE` 常數組 URL,沒有已知可達路徑;本函式是把「sink 本身」關掉,而不是
依賴每個呼叫端都記得組對 URL。

`safe_gate_path` 擋計時閘門頁回傳的相對路徑。`_BASE + path` 是字串串接,不是
URL join,所以 `path="@evil.com/x"` 會組出 `https://web.pcc.gov.tw@evil.com/x`
—— urllib 解析後 host 是 `evil.com`、`web.pcc.gov.tw` 變成 userinfo,整個
cookie jar(含 PCC 驗證 cookie)就跟著送到攻擊者主機。
"""
from __future__ import annotations

from urllib.parse import urlparse

PCC_HOST = "web.pcc.gov.tw"


class UnsafeUrlError(ValueError):
    """出站 URL 不在 https://web.pcc.gov.tw 範圍內。"""


def assert_pcc_url(url: str) -> str:
    """URL 為 PCC https 位址時原樣回傳,否則丟 UnsafeUrlError。"""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UnsafeUrlError(f"outbound scheme must be https: {parsed.scheme!r}")
    # hostname 已去掉 userinfo 與 port,故 `a@b` 這類混淆在此會現出真正的 host
    if (parsed.hostname or "").lower() != PCC_HOST:
        raise UnsafeUrlError(f"outbound host must be {PCC_HOST}")
    return url


def safe_gate_path(path: str) -> str | None:
    """閘門頁 value 可安全接在 _BASE 後面時回傳它,否則回 None。

    必須是以單一 "/" 開頭的相對路徑:
      - 不以 "/" 開頭 → 可能是 `https://...` 或 `@host`,串接後會換掉 host
      - 含 "//"       → protocol-relative,`_BASE + "//x"` 之後仍可能被當成 host
      - 含 "@"        → userinfo 混淆,真正的 host 會變成 "@" 之後那段
    """
    if not path.startswith("/") or "//" in path or "@" in path:
        return None
    return path
