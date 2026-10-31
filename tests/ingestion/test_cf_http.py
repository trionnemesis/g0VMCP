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
        self.closed = False

    def __enter__(self) -> "_FakeBody":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True

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


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("size", [0, 1023, 1024, 1025])
async def test_response_cap_boundary(method, size, monkeypatch) -> None:
    monkeypatch.setattr(cf_http, "MAX_RESPONSE_BYTES", 1024)
    getter, body = _getter_with_body(b"x" * size)
    request = (
        getter("https://web.pcc.gov.tw/detail") if method == "GET"
        else getter.post("https://web.pcc.gov.tw/search", {"tenderId": "x"})
    )
    if size > 1024:
        with pytest.raises(BlockedError, match="exceeds"):
            await request
    else:
        assert (await request).text == "x" * size
    assert body.read_sizes == [1025]
    assert body.closed


@pytest.fixture
def local_http(monkeypatch):
    """走預設 opener 的真實 urllib handler chain,只連 loopback。"""
    import http.client
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    monkeypatch.setattr(cf_http, "MAX_RESPONSE_BYTES", 1024)
    routes = {}
    calls = []
    reads = []
    original_read = http.client.HTTPResponse.read

    def tracked_read(response, amt=None):
        body = original_read(response, amt)
        reads.append((response, amt, len(body)))
        return body

    monkeypatch.setattr(http.client.HTTPResponse, "read", tracked_read)

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            calls.append((self.command, self.path, self.headers.get("Cookie")))
            status, headers, body = routes[self.path]
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = respond

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", routes, calls, reads
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("method,code", [
    ("GET", code) for code in (301, 302, 303, 307, 308)
] + [("POST", code) for code in (301, 302, 303)])
async def test_oversized_redirect_is_bounded_before_following(local_http, method, code):
    base, routes, calls, reads = local_http
    routes["/start"] = (code, {"Location": "/final"}, b"x" * 4096)
    routes["/final"] = (200, {}, b"ok")
    getter = CloudflareAwareHttpGetter(sleep=lambda _: None)
    with pytest.raises(BlockedError, match="exceeds"):
        if method == "POST":
            await getter.post(base + "/start", {"tenderId": "x"})
        else:
            await getter(base + "/start")
    assert [path for _, path, _ in calls] == ["/start"]
    assert [(amt, size) for _, amt, size in reads] == [(1025, 1025)]
    assert reads[0][0].isclosed()


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
async def test_redirect_at_cap_preserves_cookie_and_content(local_http, code):
    base, routes, calls, reads = local_http
    routes["/start"] = (
        code, {"Location": "/final", "Set-Cookie": "session=ok; Path=/"}, b"x" * 1024
    )
    routes["/final"] = (200, {}, "正常頁面".encode())
    resp = await CloudflareAwareHttpGetter()(base + "/start")
    assert resp.text == "正常頁面"
    assert calls[-1] == ("GET", "/final", "session=ok")
    assert all(amt == 1025 for _, amt, _ in reads)


@pytest.mark.parametrize("code", [301, 302, 303])
async def test_post_redirect_retains_urllib_method_policy(local_http, code):
    base, routes, calls, _ = local_http
    routes["/start"] = (code, {"Location": "/final"}, b"redirect")
    routes["/final"] = (200, {}, b"ok")
    resp = await CloudflareAwareHttpGetter().post(base + "/start", {"tenderId": "x"})
    assert resp.text == "ok"
    assert [(method, path) for method, path, _ in calls] == [
        ("POST", "/start"), ("GET", "/final")
    ]


@pytest.mark.parametrize("code", [307, 308])
async def test_post_redirect_rejection_matches_urllib(local_http, code):
    import urllib.error

    base, routes, calls, _ = local_http
    routes["/start"] = (code, {"Location": "/final"}, b"redirect")
    with pytest.raises(urllib.error.HTTPError) as error:
        await CloudflareAwareHttpGetter().post(base + "/start", {"tenderId": "x"})
    assert error.value.code == code
    assert error.value.read(4) == b"redi"
    error.value.close()
    assert len(calls) == 1


async def test_second_redirect_cannot_bypass_cap(local_http):
    base, routes, calls, reads = local_http
    routes["/start"] = (302, {"Location": "/next"}, b"redirect")
    routes["/next"] = (302, {"Location": "/final"}, b"x" * 4096)
    routes["/final"] = (200, {}, b"ok")
    with pytest.raises(BlockedError, match="exceeds"):
        await CloudflareAwareHttpGetter()(base + "/start")
    assert [path for _, path, _ in calls] == ["/start", "/next"]
    assert all(amt == 1025 for _, amt, _ in reads)
    assert all(response.isclosed() for response, _, _ in reads)


async def test_post_gate_flow_reuses_cookie_jar(local_http, monkeypatch):
    base, routes, calls, _ = local_http
    monkeypatch.setattr(cf_http, "_BASE", base)
    routes["/search"] = (302, {"Location": "/gate"}, b"redirect")
    routes["/gate"] = (200, {}, _GATE_HTML.encode())
    routes["/tps/validate/check?token=abc"] = (
        200, {"Set-Cookie": "verified=yes; Path=/"}, b"validated"
    )
    getter = CloudflareAwareHttpGetter(sleep=lambda _: None)
    original_open = getter._opener.open

    def open_with_validated_page(req, *args, **kwargs):
        if req.full_url == base + "/search" and len(calls) >= 3:
            routes["/search"] = (200, {}, "開標時間 正常搜尋結果".encode())
        return original_open(req, *args, **kwargs)

    monkeypatch.setattr(getter._opener, "open", open_with_validated_page)
    resp = await getter.post(base + "/search", {"tenderId": "x"})
    assert "正常搜尋結果" in resp.text
    assert calls[-1] == ("POST", "/search", "verified=yes")


async def test_pcc_search_detail_fixture_through_real_getter(local_http, monkeypatch):
    """合成搜尋頁 + 已錄製明細 fixture;不宣稱正式 PCC 連線已驗證。"""
    from g0vmcp.ingestion import fetcher as fetcher_module
    from g0vmcp.ingestion.fetcher import PccHttpFetcher

    base, routes, calls, _ = local_http
    monkeypatch.setattr(cf_http, "MAX_RESPONSE_BYTES", 8 * 1024 * 1024)
    monkeypatch.setattr(fetcher_module, "_BASE", base)
    detail_path = "/tpam/detail?orgId=3.80.11&caseNo=1130108-5"
    search = (
        f'<table><tr><td>1130108-5</td><td><a href="{detail_path}">'
        '明細</a></td></tr></table>'
    ).encode()
    routes["/prkms/tender/common/basic/readTenderBasic"] = (200, {}, search)
    routes[detail_path] = (200, {}, load_fixture("live_tender_detail.html").encode())
    detail = await PccHttpFetcher(CloudflareAwareHttpGetter()).fetch_detail("1130108-5", None)
    assert detail.title == "昆陽大樓整建工程採購案"
    assert detail.tender_id.org_id == "3.80.11"
    assert detail.source_url == base + detail_path
    assert [method for method, _, _ in calls] == ["POST", "GET"]
