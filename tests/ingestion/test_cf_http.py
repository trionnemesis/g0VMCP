"""CloudflareAwareHttpGetter — 驗證計時閘門偵測/通過/重試耗盡。

底層 urllib GET 以 monkeypatch 替換為回傳 fixture/合成 HTML,絕不真的連網。
"""
from __future__ import annotations

import pytest

from g0vmcp.contracts import BlockedError
from g0vmcp.ingestion import cf_http
from g0vmcp.ingestion.cf_http import CloudflareAwareHttpGetter

from .conftest import load_fixture

# 計時閘門頁:含 validate/check 端點與 id="url" 的後續驗證連結
_GATE_HTML = (
    '<html><body>請稍候<form>'
    '<input id="url" value="/tps/validate/check?token=abc"/>'
    '</form>請至 /tps/validate/check 完成驗證</body></html>'
)
# 找不到驗證 URL 的閘門頁(結構異常,非速率封鎖)
_GATE_HTML_NO_URL = (
    "<html><body>/tps/validate/check 但無 url 欄位</body></html>"
)


def _make_getter(pages: list[str]) -> CloudflareAwareHttpGetter:
    """以預錄回應序列驅動底層 GET;sleep 為 no-op。"""
    getter = CloudflareAwareHttpGetter(sleep=lambda _s: None)
    calls = {"n": 0}

    def fake_raw_get(url: str) -> str:
        i = calls["n"]
        calls["n"] += 1
        return pages[min(i, len(pages) - 1)]

    getter._raw_get = fake_raw_get  # type: ignore[assignment]
    getter._calls = calls  # type: ignore[attr-defined]
    return getter


async def test_normal_page_returns_resp_200() -> None:
    getter = _make_getter([load_fixture("live_tender_detail.html")])

    resp = await getter("https://web.pcc.gov.tw/detail")

    assert resp.status_code == 200
    assert "昆陽大樓整建工程" in resp.text


async def test_gate_is_passed_then_returns_content() -> None:
    # 第一次回閘門頁 → 通過後第二次回正常明細頁
    getter = _make_getter([_GATE_HTML, load_fixture("live_tender_detail.html")])

    resp = await getter("https://web.pcc.gov.tw/detail")

    assert resp.status_code == 200
    assert "昆陽大樓整建工程" in resp.text
    # 通過閘門需多打一次 validate/check + 重抓 → 至少 3 次底層 GET
    assert getter._calls["n"] >= 3  # type: ignore[attr-defined]


async def test_gate_retry_exhausted_raises_blocked_error() -> None:
    # 持續回閘門頁,重試耗盡
    getter = _make_getter([_GATE_HTML])

    with pytest.raises(BlockedError):
        await getter("https://web.pcc.gov.tw/detail")


async def test_gate_without_validate_url_returns_page_not_blocked() -> None:
    # 找不到驗證 URL → 視為結構異常直接回頁,不拋 BlockedError
    getter = _make_getter([_GATE_HTML_NO_URL])

    resp = await getter("https://web.pcc.gov.tw/detail")

    assert resp.status_code == 200
    assert "/tps/validate/check" in resp.text


async def test_satisfies_http_getter_protocol() -> None:
    from g0vmcp.ingestion.http import HttpGetter

    getter = CloudflareAwareHttpGetter(sleep=lambda _s: None)
    assert isinstance(getter, HttpGetter)


# ── 回應大小上限(CWE-400 資源耗用護欄)──────────────────────────────────────


class _FakeBody:
    """最小 urllib 回應替身:只實作 context manager + read(n)。"""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.read_sizes: list[int | None] = []

    def __enter__(self) -> "_FakeBody":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self, n: int | None = None) -> bytes:
        self.read_sizes.append(n)
        return self._payload if n is None else self._payload[:n]


class _FakeOpener:
    def __init__(self, body: _FakeBody) -> None:
        self._body = body

    def open(self, _req: object, timeout: int | None = None) -> _FakeBody:
        return self._body


def _getter_with_body(payload: bytes) -> tuple[CloudflareAwareHttpGetter, _FakeBody]:
    body = _FakeBody(payload)
    getter = CloudflareAwareHttpGetter(
        opener=_FakeOpener(body), sleep=lambda _s: None
    )
    return getter, body


async def test_oversized_response_is_aborted() -> None:
    """超過上限的回應不得被整份讀入,必須拋 BlockedError。"""
    getter, _ = _getter_with_body(b"x" * (cf_http.MAX_RESPONSE_BYTES + 10))

    with pytest.raises(BlockedError, match="exceeds"):
        await getter("https://web.pcc.gov.tw/detail")


async def test_read_is_bounded_not_unlimited() -> None:
    """read() 必須帶上限引數 — 絕不呼叫無上限的 read()。"""
    getter, body = _getter_with_body(b"<html>ok</html>")

    await getter("https://web.pcc.gov.tw/detail")

    assert body.read_sizes == [cf_http.MAX_RESPONSE_BYTES + 1]
    assert None not in body.read_sizes


async def test_normal_sized_page_is_unaffected() -> None:
    """上限遠高於真實頁面 — 正常頁行為不變(回歸保護)。"""
    getter, _ = _getter_with_body(
        load_fixture("live_tender_detail.html").encode("utf-8")
    )

    resp = await getter("https://web.pcc.gov.tw/detail")

    assert resp.status_code == 200
    assert "昆陽大樓整建工程" in resp.text


async def test_post_read_is_also_bounded() -> None:
    """POST 路徑共用同一上限(兩處 raw 方法都不得無上限讀取)。"""
    getter, _ = _getter_with_body(b"y" * (cf_http.MAX_RESPONSE_BYTES + 1))

    with pytest.raises(BlockedError, match="exceeds"):
        await getter.post("https://web.pcc.gov.tw/search", {"tenderId": "x"})
