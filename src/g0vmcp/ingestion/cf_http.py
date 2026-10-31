"""CloudflareAwareHttpGetter — 實作 ingestion.http.HttpGetter Protocol。

web.pcc.gov.tw 對密集請求回「計時閘門」頁(/tps/validate/check);通過後設驗證
cookie,沿用同一 cookie jar 才能保留。本 class 把 scripts/enrich_open_date.py 的
閘門偵測/通過/重試邏輯萃取為可注入的 async getter。底層為同步 urllib,以
asyncio.to_thread 包成 async,不阻塞事件迴圈。重試耗盡 → 拋 contracts.BlockedError。
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


def _read_capped_bytes(response, url: str, amt: int | None = None) -> bytes:
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
        return _read_capped_bytes(self._response, self._url, amt)


class _BoundedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """沿用 urllib 的轉址目的地、方法、cookie 與迴圈政策,限制中間回應。"""

    def http_error_302(self, req, fp, code, msg, headers):
        return super().http_error_302(
            req, _BoundedRedirectResponse(fp, req.full_url), code, msg, headers
        )

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


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
        self._opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            _BoundedRedirectHandler(),
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
        return _read_capped_bytes(r, url).decode("utf-8", "replace")

    def _raw_get(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with self._opener.open(req, timeout=40) as r:
            return self._read_capped(r, url)

    def _raw_post(self, url: str, data: dict) -> str:
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(
            url,
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
        if not path.startswith("/") or "//" in path or "@" in path:
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
