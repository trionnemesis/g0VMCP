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
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self._sleep = sleep
        self._max_retry = max_retry

    # ------------------------------------------------------------------
    # 同步底層(urllib)
    # ------------------------------------------------------------------
    def _raw_get(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with self._opener.open(req, timeout=40) as r:
            return r.read().decode("utf-8", "replace")

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
            return r.read().decode("utf-8", "replace")

    def _pass_gate(self, html: str) -> bool:
        """偵測閘門 → 等候後請求 validate/check 取驗證 cookie。回是否已嘗試通過。"""
        m = _GATE_RE.search(html)
        if not m:
            return False  # 找不到驗證 URL → 非速率封鎖,結構異常,不重試
        self._sleep(_GATE_WAIT_SECONDS)  # 尊重速率限制的計時等候
        self._raw_get(_BASE + _htmllib.unescape(m.group(1)))
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
