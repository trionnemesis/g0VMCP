"""CloudflareAwareHttpGetter 對外請求邊界的安全回歸測試。

修正前:
  1. build_opener() 預設帶 FileHandler / FTPHandler,file:// 與 ftp:// 皆可達
     (CWE-22 本機檔案讀取 / CWE-918 SSRF)。
  2. 預設 HTTPRedirectHandler 會跟隨 Location 到任意 http/https/ftp 主機,
     上游 open redirect 即可把請求導向 127.0.0.1 / 169.254.169.254。

修正後:所有網路邊界(GET/POST/redirect)一律要求 https + web.pcc.gov.tw。
測試以真實 opener 行為驗證,但不連外網 —— 被拒的 URL 在送出前就被攔下。
"""
from __future__ import annotations

import urllib.request

import pytest

from g0vmcp.contracts import BlockedError
from g0vmcp.ingestion.cf_http import (
    CloudflareAwareHttpGetter,
    _PinnedRedirectHandler,
    _require_pcc_url,
)

VALID = "https://web.pcc.gov.tw/tps/tender/common/bulletion/readBulletion?caseNo=1"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",                      # 本機檔案讀取
        "file://web.pcc.gov.tw/etc/passwd",
        "ftp://web.pcc.gov.tw/x",                  # urllib 預設可達的 scheme
        "http://web.pcc.gov.tw/x",                 # 降級為明文
        "https://169.254.169.254/latest/meta-data/",  # 雲端 metadata
        "https://127.0.0.1:8000/admin",
        "https://localhost/admin",
        "https://attacker.example/x",
        "https://web.pcc.gov.tw.attacker.example/x",  # 後綴混淆
        "https://attacker.example/web.pcc.gov.tw",    # 路徑混淆
        "https://user:pw@web.pcc.gov.tw/x",           # URL 內夾帶憑證
    ],
)
def test_outbound_url_outside_pcc_is_refused(url: str) -> None:
    with pytest.raises(BlockedError):
        _require_pcc_url(url)


@pytest.mark.parametrize(
    "url",
    [
        VALID,
        "https://web.pcc.gov.tw/tps/validate/check?token=abc",
        "https://WEB.PCC.GOV.TW/x",   # host 比對不分大小寫
        "https://web.pcc.gov.tw./x",  # 尾綴點的 FQDN 寫法
    ],
)
def test_legitimate_pcc_urls_still_allowed(url: str) -> None:
    """修正不得擋掉正常的 PCC 端點。"""
    assert _require_pcc_url(url) == url


def test_raw_get_refuses_before_opening_any_connection() -> None:
    """_raw_get 必須在動用 opener 前就拒絕 —— 用會爆的 opener 證明它沒被呼叫。"""
    class ExplodingOpener:
        def open(self, *a, **kw):  # noqa: ANN002, ANN003
            raise AssertionError("opener must not be reached for a refused URL")

    getter = CloudflareAwareHttpGetter(opener=ExplodingOpener(), sleep=lambda _s: None)
    with pytest.raises(BlockedError):
        getter._raw_get("file:///etc/passwd")


def test_raw_post_refuses_before_opening_any_connection() -> None:
    class ExplodingOpener:
        def open(self, *a, **kw):  # noqa: ANN002, ANN003
            raise AssertionError("opener must not be reached for a refused URL")

    getter = CloudflareAwareHttpGetter(opener=ExplodingOpener(), sleep=lambda _s: None)
    with pytest.raises(BlockedError):
        getter._raw_post("https://attacker.example/x", {"a": "b"})


def test_redirect_off_host_is_refused() -> None:
    """Location 指向站外時,redirect handler 必須拒絕而非跟隨。"""
    handler = _PinnedRedirectHandler()
    req = urllib.request.Request(VALID)
    with pytest.raises(BlockedError):
        handler.redirect_request(
            req, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data/"
        )


def test_redirect_within_pcc_is_still_followed() -> None:
    """同站 redirect 仍須正常運作(PCC 的閘門流程依賴它)。"""
    handler = _PinnedRedirectHandler()
    req = urllib.request.Request(VALID)
    new = handler.redirect_request(
        req, None, 302, "Found", {}, "https://web.pcc.gov.tw/tps/validate/check"
    )
    assert new is not None
    assert new.full_url == "https://web.pcc.gov.tw/tps/validate/check"


def test_default_opener_uses_the_pinned_redirect_handler() -> None:
    """守門:預設 opener 必須裝上釘住的 handler,且不得保留 file/ftp handler。"""
    getter = CloudflareAwareHttpGetter(sleep=lambda _s: None)
    handlers = getter._opener.handlers
    assert any(isinstance(h, _PinnedRedirectHandler) for h in handlers)
    # 預設的 HTTPRedirectHandler 必須已被取代,而非並存
    plain = [
        h
        for h in handlers
        if isinstance(h, urllib.request.HTTPRedirectHandler)
        and not isinstance(h, _PinnedRedirectHandler)
    ]
    assert plain == []
