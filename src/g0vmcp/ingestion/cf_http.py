"""CloudflareAwareHttpGetter — 實作 ingestion.http.HttpGetter Protocol。

web.pcc.gov.tw 對密集請求回「計時閘門」頁(/tps/validate/check);通過後設驗證
cookie,沿用同一 cookie jar 才能保留。本 class 把 scripts/enrich_open_date.py 的
閘門偵測/通過/重試邏輯萃取為可注入的 async getter。底層為同步 urllib,以
asyncio.to_thread 包成 async,不阻塞事件迴圈。重試耗盡 → 拋 contracts.BlockedError。

出站邊界有三道互補的護欄,原本分散在兩個 PR 裡,此處合為一處:
  1. `_require_pcc_url` —— host/scheme 釘選。urllib 的 build_opener 預設帶
     FileHandler 與 FTPHandler,`file:///etc/passwd` 是可達的 scheme;且預設
     redirect handler 會跟隨 Location 到任意主機(CWE-918 / CWE-22)。
  2. `is_safe_relative_path` —— 上游 HTML 給的相對路徑能否安全接在 _BASE 後。
     `@` 會讓 base 變成 userinfo,一個 href 就能換掉 host。
  3. `read_capped_bytes` —— 回應(含轉址中間回應)讀取上限(CWE-400)。
`PinnedBoundedRedirectHandler` 同時執行 1 與 3:驗證轉址目標,並限制跟隨前
被 drain 的 body。
"""
from __future__ import annotations

import asyncio
import html as _htmllib
import http.cookiejar
import re
import time
import urllib.parse
import urllib.request

from g0vmcp.contracts import BlockedError
from g0vmcp.ingestion.http import Resp

_BASE = "https://web.pcc.gov.tw"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)
_GATE_RE = re.compile(r'id="url"[^>]*value="([^"]*)"')
_GATE_MAX_RETRY = 3
_GATE_WAIT_SECONDS = 3.0
# 單次回應讀取上限(含轉址中間回應)。既有明細 fixture 小於 8 MiB;
# 正式來源的所有頁面大小並未逐一驗證。無上限讀取可造成資源耗用(CWE-400)。
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> tuple[str, str, int | None]:
    """(scheme, host, port) —— port 為 None 時補上該 scheme 的預設埠。

    正規化後才比得出 `https://web.pcc.gov.tw` 與 `https://web.pcc.gov.tw:443`
    是同一個 origin;host 比對不分大小寫,並去掉 FQDN 尾綴點。
    """
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as exc:  # 埠號非數字 → 視為不合法 URL
        raise BlockedError(f"refusing outbound request with bad port: {url!r}") from exc
    return scheme, host, port if port is not None else _DEFAULT_PORTS.get(scheme)


def _require_pcc_url(url: str) -> str:
    """只允許與 _BASE 同一個 origin 的 URL 真正送出請求。

    深度防禦(CWE-918 / CWE-22):
      1. urllib.request.build_opener() 預設帶 FileHandler 與 FTPHandler,
         因此 file:///etc/passwd、ftp://… 都是可達的 scheme;呼叫端一旦
         把外部字串傳進來就變成本機檔案讀取。
      2. 預設的 HTTPRedirectHandler 會跟隨 Location 到任意 http/https/ftp
         主機 —— 上游 open redirect 或站台被入侵即可把請求導向
         127.0.0.1 / 169.254.169.254。
    呼叫端(fetcher)已對解析出的 href 做相對路徑檢查;本函式是網路邊界上
    最後一道,同時覆蓋 redirect 目標。

    允許的 origin 在「呼叫時」由 _BASE 推導,而不是 import 時算好存成常數:
    兩者在正式環境完全等價(_BASE 是常數,仍然只有 https://web.pcc.gov.tw
    過關),但 import 時算好的衍生常數會與被 monkeypatch 的 _BASE 不同步 ——
    既有的轉址測試以本機 HTTP server 驅動真實 urllib 行為,正是靠
    `monkeypatch.setattr(cf_http, "_BASE", ...)` 改寫基底。衍生常數會讓那組
    測試改成測不到真實轉址路徑,等於為了設定方便而犧牲測試強度。
    """
    want_scheme, want_host, want_port = _origin(_BASE)
    scheme, host, port = _origin(url)
    if (scheme, host, port) != (want_scheme, want_host, want_port):
        raise BlockedError(f"refusing outbound request outside {_BASE}: {url!r}")
    parsed = urllib.parse.urlparse(url)
    if parsed.username is not None or parsed.password is not None:
        raise BlockedError("refusing outbound request carrying URL credentials")
    return url


def is_safe_relative_path(path: str) -> bool:
    """上游 HTML 給的路徑是否能安全地接在 _BASE 後面。

    不是潔癖檢查:`@` 會讓 `https://web.pcc.gov.tw` 變成 userinfo,
    `https://web.pcc.gov.tw@evil.com/x` 的實際 host 是 evil.com
    —— 一個上游 href 就能把請求帶離站外(CWE-918 SSRF / CWE-601)。
    `//` 開頭則是 protocol-relative,同樣換掉 host。

    與 `_require_pcc_url` 是互補而非重複:本函式在「組 URL 之前」擋掉壞路徑
    (呼叫端因此能安然跳過該筆資料),`_require_pcc_url` 則是送出前的最後防線。

    公開給 scripts/ 共用:同樣的判斷先前在 fetcher 與本模組各寫一份,
    維運腳本漏掉,正是這次要消掉的重複。
    """
    return path.startswith("/") and "//" not in path and "@" not in path


def read_capped_bytes(response, url: str, amt: int | None = None) -> bytes:
    limit = MAX_RESPONSE_BYTES + 1
    if amt is not None and amt >= 0:
        limit = min(amt, limit)
    raw = response.read(limit)
    if len(raw) > MAX_RESPONSE_BYTES:
        response.close()
        raise BlockedError(
            f"response exceeds {MAX_RESPONSE_BYTES} bytes, aborted: {url}"
        )
    return raw


class _BoundedRedirectResponse:
    """保留回應介面,只限制 urllib 跟隨轉址前的 body drain。"""

    def __init__(self, response, url: str) -> None:
        self._response = response
        self._url = url

    def __getattr__(self, name):
        return getattr(self._response, name)

    def read(self, amt: int | None = None) -> bytes:
        return read_capped_bytes(self._response, self._url, amt)


class PinnedBoundedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """轉址時同時做兩件事:驗證目標、限制中間回應。

    兩者都必須在同一個 handler 裡 —— build_opener 以傳入的 handler 取代同類的
    預設 handler,所以掛兩個各做一半的 redirect handler,後者會頂掉前者,
    其中一道護欄會靜默失效。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _require_pcc_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

    def http_error_302(self, req, fp, code, msg, headers):
        return super().http_error_302(
            req, _BoundedRedirectResponse(fp, req.full_url), code, msg, headers
        )

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


# 兩個來源 PR 各自用過的名稱都保留為別名:外部已有引用,改名不應是破壞性變更。
BoundedRedirectHandler = PinnedBoundedRedirectHandler
_BoundedRedirectHandler = PinnedBoundedRedirectHandler
_PinnedRedirectHandler = PinnedBoundedRedirectHandler
_read_capped_bytes = read_capped_bytes


def _is_gate(html: str) -> bool:
    """計時閘門頁特徵:含 validate/check 端點且尚未載入明細(無開標時間)。"""
    return "/tps/validate/check" in html and "開標時間" not in html


class CloudflareAwareHttpGetter:
    """注入式 async HTTP getter,內建 PCC 計時閘門通過邏輯。

    Args:
        opener: urllib opener(可注入測試替身);預設建立帶 cookie jar 的 opener。
        sleep: 計時等候函式(可注入,測試以 no-op 避免真的 sleep)。
        max_retry: 閘門重試上限,耗盡 → BlockedError。
    """

    def __init__(
        self,
        *,
        opener: urllib.request.OpenerDirector | None = None,
        sleep=time.sleep,
        max_retry: int = _GATE_MAX_RETRY,
    ) -> None:
        # build_opener 會以傳入的 handler 取代同類的預設 handler,
        # 故 PinnedBoundedRedirectHandler 會頂掉預設的 HTTPRedirectHandler。
        self._opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            PinnedBoundedRedirectHandler(),
        )
        self._sleep = sleep
        self._max_retry = max_retry

    # ------------------------------------------------------------------
    # 同步底層(urllib)
    # ------------------------------------------------------------------
    def _read_capped(self, r, url: str) -> str:
        """讀取回應但不超過 MAX_RESPONSE_BYTES;超量即中止並拋 BlockedError。

        多讀 1 byte 以區分「正好等於上限」與「超過上限」。
        """
        return read_capped_bytes(r, url).decode("utf-8", "replace")

    def _raw_get(self, url: str) -> str:
        req = urllib.request.Request(
            _require_pcc_url(url), headers={"User-Agent": _UA}
        )
        with self._opener.open(req, timeout=40) as r:
            return self._read_capped(r, url)

    def _raw_post(self, url: str, data: dict) -> str:
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(
            _require_pcc_url(url),
            data=body,
            headers={
                "User-Agent": _UA,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with self._opener.open(req, timeout=40) as r:
            return self._read_capped(r, url)

    def _pass_gate(self, html: str) -> bool:
        """偵測閘門 → 等候後請求 validate/check 取驗證 cookie。回是否已嘗試通過。"""
        m = _GATE_RE.search(html)
        if not m:
            return False  # 找不到驗證 URL → 非速率封鎖,結構異常,不重試
        path = _htmllib.unescape(m.group(1))
        if not is_safe_relative_path(path):
            return False
        self._sleep(_GATE_WAIT_SECONDS)  # 尊重速率限制的計時等候
        self._raw_get(_BASE + path)
        return True

    def _get_through_gate(self, url: str) -> str:
        html = self._raw_get(url)
        for _ in range(self._max_retry):
            if not _is_gate(html):
                return html
            if not self._pass_gate(html):
                return html  # 非速率封鎖,直接回原始頁
            html = self._raw_get(url)
        raise BlockedError(
            f"rate-limited after {self._max_retry} gate retries: {url}"
        )

    def _post_through_gate(self, url: str, data: dict) -> str:
        html = self._raw_post(url, data)
        for _ in range(self._max_retry):
            if not _is_gate(html):
                return html
            if not self._pass_gate(html):
                return html
            html = self._raw_post(url, data)
        raise BlockedError(
            f"rate-limited after {self._max_retry} gate retries (POST): {url}"
        )

    # ------------------------------------------------------------------
    # async HttpGetter Protocol
    # ------------------------------------------------------------------
    async def __call__(self, url: str) -> Resp:
        html = await asyncio.to_thread(self._get_through_gate, url)
        return Resp(status_code=200, text=html, url=url)

    async def post(self, url: str, data: dict) -> Resp:
        html = await asyncio.to_thread(self._post_through_gate, url, data)
        return Resp(status_code=200, text=html, url=url)
