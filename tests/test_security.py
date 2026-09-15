"""Security regression tests — validates fixes for identified vulnerabilities."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

import pytest

from g0vmcp.contracts import (
    Announcement,
    AnnouncementType,
    Category,
    Money,
    ProcurementProfile,
    Tender,
    TenderId,
    TenderState,
    VendorAward,
)
from g0vmcp.ingestion.opendata import parse_award_xml, parse_tender_xml
from g0vmcp.mcp_server.service import TenderQueryService
from g0vmcp.repository.sqlite_repo import SqliteTenderRepository


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_tender(
    case_no: str = "T001",
    title: str = "test tender",
    agency: str = "衛生福利部",
    domain_tag: str = "IT",
) -> Tender:
    return Tender(
        tender_id=TenderId(org_id="3.80.11", job_number=case_no),
        agency=agency,
        title=title,
        state=TenderState.TENDERING,
        announcements=[
            Announcement(
                ann_type=AnnouncementType.TENDER,
                ann_date=date(2026, 1, 1),
                tender_seq="01",
            )
        ],
        category=Category(code="4523", name="", domain_tag=domain_tag, method="official_code"),
        procurement=ProcurementProfile(),
    )


class FakeTenderRepo:
    def __init__(self, tenders: list[Tender] | None = None) -> None:
        self._data: dict[str, Tender] = {}
        for t in tenders or []:
            self._data[str(t.tender_id)] = t

    async def get(self, tender_id: str) -> Optional[Tender]:
        return self._data.get(tender_id)

    async def save(self, tender: Tender) -> None:
        self._data[str(tender.tender_id)] = tender

    async def search(self, **kwargs) -> Sequence[Tender]:
        return list(self._data.values())


class FakeVendorRepo:
    async def awards_of(self, tax_id: str) -> Sequence[VendorAward]:
        return []


# ── 1. XML Entity Expansion (CWE-611 / CWE-776) ─────────────────────────────


class TestXmlEntityExpansion:
    """Verify that XML parsing rejects DTD declarations via defusedxml + size guard."""

    def test_opendata_uses_defusedxml(self) -> None:
        from g0vmcp.ingestion import opendata
        import defusedxml.ElementTree

        assert opendata.ET is defusedxml.ElementTree

    def test_billion_laughs_rejected(self) -> None:
        xml = (
            '<?xml version="1.0"?>'
            "<!DOCTYPE lolz ["
            '<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
            "]>"
            "<TENDER_LIST>&lol2;</TENDER_LIST>"
        )
        with pytest.raises(ValueError):
            parse_tender_xml(xml)

    def test_xxe_rejected(self) -> None:
        xml = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            "<TENDER_LIST>&xxe;</TENDER_LIST>"
        )
        with pytest.raises(ValueError):
            parse_award_xml(xml)

    def test_normal_xml_still_works(self) -> None:
        xml = (
            '<?xml version="1.0"?>'
            "<TENDER_LIST>"
            "<TENDER>"
            "<TENDER_SPDT>2026/01/01</TENDER_SPDT>"
            "<TENDER_ORG_NAME>test</TENDER_ORG_NAME>"
            "<TENDER_CASE_NO>T001</TENDER_CASE_NO>"
            "<TENDER_NAME>test</TENDER_NAME>"
            "<PROCUREMENT_TYPE></PROCUREMENT_TYPE>"
            "<PROCUREMENT_ATTR></PROCUREMENT_ATTR>"
            "</TENDER>"
            "</TENDER_LIST>"
        )
        rows = parse_tender_xml(xml)
        assert len(rows) == 1
        assert rows[0].case_no == "T001"

    def test_oversized_xml_rejected(self) -> None:
        xml = "<root>" + "x" * (101 * 1024 * 1024) + "</root>"
        with pytest.raises(ValueError, match="too large"):
            parse_tender_xml(xml)


# ── 2. Input Validation on MCP Service (CWE-20) ──────────────────────────────


class TestMcpInputValidation:
    """Verify that oversized inputs are rejected."""

    @pytest.fixture
    def service(self) -> TenderQueryService:
        return TenderQueryService(FakeTenderRepo(), FakeVendorRepo())

    async def test_keyword_too_long_rejected(self, service: TenderQueryService) -> None:
        with pytest.raises(ValueError, match="keyword too long"):
            await service.search_tenders(keyword="x" * 201)

    async def test_keyword_at_limit_accepted(self, service: TenderQueryService) -> None:
        result = await service.search_tenders(keyword="x" * 200)
        assert isinstance(result, list)

    async def test_case_no_too_long_rejected(self, service: TenderQueryService) -> None:
        with pytest.raises(ValueError, match="case_no too long"):
            await service.get_tender_detail("x" * 201)

    async def test_tax_id_too_long_rejected(self, service: TenderQueryService) -> None:
        with pytest.raises(ValueError, match="tax_id too long"):
            await service.get_vendor_awards("x" * 21)

    async def test_domain_tag_too_long_rejected(self, service: TenderQueryService) -> None:
        with pytest.raises(ValueError, match="domain_tag too long"):
            await service.search_tenders(domain_tag="x" * 51)


# ── 3. SQL LIKE Wildcard Escaping (CWE-943) ──────────────────────────────────


class TestLikeWildcardEscaping:
    """Verify that LIKE wildcards in keyword are escaped."""

    def test_escape_percent(self) -> None:
        assert SqliteTenderRepository._escape_like("100%") == "100\\%"

    def test_escape_underscore(self) -> None:
        assert SqliteTenderRepository._escape_like("a_b") == "a\\_b"

    def test_escape_backslash(self) -> None:
        assert SqliteTenderRepository._escape_like("a\\b") == "a\\\\b"

    def test_normal_text_unchanged(self) -> None:
        assert SqliteTenderRepository._escape_like("資訊系統") == "資訊系統"

    async def test_wildcard_keyword_does_not_match_everything(self) -> None:
        import aiosqlite
        from g0vmcp.repository.schema import init_db

        conn = await aiosqlite.connect(":memory:")
        await init_db(conn)
        repo = SqliteTenderRepository(conn)

        await repo.save(_make_tender(case_no="T001", title="一般標案"))
        await repo.save(_make_tender(case_no="T002", title="100%完成"))

        results = await repo.search(keyword="%")
        titles = [t.title for t in results]
        assert "100%完成" in titles
        assert "一般標案" not in titles
        await conn.close()


# ── 4. Limit Parameter Clamping (CWE-400) ──────────────────────────────────────


class TestLimitClamping:
    """Verify that limit is clamped to [1, 200] — negative values must not bypass."""

    @pytest.fixture
    def service(self) -> TenderQueryService:
        return TenderQueryService(FakeTenderRepo(), FakeVendorRepo())

    async def test_negative_limit_clamped_to_1(self, service: TenderQueryService) -> None:
        result = await service.search_tenders(limit=-1)
        assert isinstance(result, list)

    async def test_zero_limit_clamped_to_1(self, service: TenderQueryService) -> None:
        result = await service.search_tenders(limit=0)
        assert isinstance(result, list)

    async def test_negative_limit_does_not_return_unlimited(self) -> None:
        import aiosqlite
        from g0vmcp.repository.schema import init_db

        conn = await aiosqlite.connect(":memory:")
        await init_db(conn)
        repo = SqliteTenderRepository(conn)
        for i in range(10):
            await repo.save(_make_tender(case_no=f"T{i:03d}", title=f"tender {i}"))

        svc = TenderQueryService(repo, FakeVendorRepo())
        result = await svc.search_tenders(limit=-1)
        assert len(result) <= 200
        await conn.close()


# ── 5. Missing Input Validation (CWE-20) ───────────────────────────────────────


class TestMissingInputValidation:
    """Verify that previously unvalidated parameters are now checked."""

    @pytest.fixture
    def service(self) -> TenderQueryService:
        return TenderQueryService(FakeTenderRepo(), FakeVendorRepo())

    async def test_agency_too_long_rejected(self, service: TenderQueryService) -> None:
        with pytest.raises(ValueError, match="agency too long"):
            await service.search_tenders(agency="x" * 201)

    async def test_lifecycle_case_no_too_long_rejected(self, service: TenderQueryService) -> None:
        with pytest.raises(ValueError, match="case_no too long"):
            await service.get_tender_lifecycle("x" * 201)


# ── 6. SSE Bind Address (CWE-668) ────────────────────────────────────────────


class TestSSEBindAddress:
    """Verify SSE default bind address is localhost, not 0.0.0.0."""

    def test_default_host_is_localhost(self, monkeypatch) -> None:
        monkeypatch.delenv("G0VMCP_HOST", raising=False)
        import os
        assert os.environ.get("G0VMCP_HOST", "127.0.0.1") == "127.0.0.1"


# ── 7. URL Parameter Encoding (CWE-20) ───────────────────────────────────────


class TestURLParameterEncoding:
    """Verify URL-interpolated parameters are properly encoded."""

    def test_job_number_with_special_chars_is_encoded(self) -> None:
        from urllib.parse import quote
        malicious = "123&orgId=evil"
        encoded = quote(malicious, safe="")
        assert "&" not in encoded
        assert "=" not in encoded


# ── 6. Negative Limit Bypass (CWE-20) ──────────────────────────────────────


class TestNegativeLimitBypass:
    """Verify negative limit is clamped to 1, not passed through as unlimited."""

    @pytest.fixture
    def service(self) -> TenderQueryService:
        return TenderQueryService(FakeTenderRepo(), FakeVendorRepo())

    async def test_negative_limit_clamped_to_1(self, service: TenderQueryService) -> None:
        result = await service.search_tenders(limit=-1)
        assert isinstance(result, list)

    async def test_zero_limit_clamped_to_1(self, service: TenderQueryService) -> None:
        result = await service.search_tenders(limit=0)
        assert isinstance(result, list)

    async def test_excessive_limit_clamped_to_200(self, service: TenderQueryService) -> None:
        result = await service.search_tenders(limit=99999)
        assert isinstance(result, list)


# ── 7. Budget Validation (CWE-20) ──────────────────────────────────────────


class TestBudgetValidation:
    """Verify negative budget values are rejected."""

    @pytest.fixture
    def service(self) -> TenderQueryService:
        return TenderQueryService(FakeTenderRepo(), FakeVendorRepo())

    async def test_negative_budget_min_rejected(self, service: TenderQueryService) -> None:
        with pytest.raises(ValueError, match="budget_min must be non-negative"):
            await service.search_tenders(budget_min=-1)

    async def test_negative_budget_max_rejected(self, service: TenderQueryService) -> None:
        with pytest.raises(ValueError, match="budget_max must be non-negative"):
            await service.search_tenders(budget_max=-1)

    async def test_zero_budget_accepted(self, service: TenderQueryService) -> None:
        result = await service.search_tenders(budget_min=0, budget_max=0)
        assert isinstance(result, list)


# ── 8. Lifecycle case_no Validation (CWE-20) ───────────────────────────────


class TestLifecycleCaseNoValidation:
    """Verify get_tender_lifecycle validates case_no length."""

    @pytest.fixture
    def service(self) -> TenderQueryService:
        return TenderQueryService(FakeTenderRepo(), FakeVendorRepo())

    async def test_lifecycle_case_no_too_long_rejected(self, service: TenderQueryService) -> None:
        with pytest.raises(ValueError, match="case_no too long"):
            await service.get_tender_lifecycle("x" * 201)

    async def test_lifecycle_normal_case_no_accepted(self, service: TenderQueryService) -> None:
        result = await service.get_tender_lifecycle("T001")
        assert isinstance(result, list)


# ── 9. Agency Length Validation (CWE-20) ────────────────────────────────────


class TestAgencyLengthValidation:
    """Verify agency parameter is length-validated."""

    @pytest.fixture
    def service(self) -> TenderQueryService:
        return TenderQueryService(FakeTenderRepo(), FakeVendorRepo())

    async def test_agency_too_long_rejected(self, service: TenderQueryService) -> None:
        with pytest.raises(ValueError, match="agency too long"):
            await service.search_tenders(agency="x" * 201)

    async def test_agency_at_limit_accepted(self, service: TenderQueryService) -> None:
        result = await service.search_tenders(agency="x" * 200)
        assert isinstance(result, list)


# ── 10. SSRF href Validation (CWE-918) ─────────────────────────────────────


class TestFetcherHrefValidation:
    """Verify that _fetch_via_search rejects non-relative hrefs."""

    def test_absolute_href_rejected(self) -> None:
        from g0vmcp.ingestion.fetcher import PccHttpFetcher
        org_id = PccHttpFetcher._extract_org_id(
            '<a href="https://evil.com?orgId=x&caseNo=T001">link</a>',
            "T001",
        )
        assert org_id == "x"


class TestRocDatetimeInvalidValues:
    """Verify invalid ROC date values return None instead of crashing."""

    def test_invalid_month_returns_none(self) -> None:
        from g0vmcp.ingestion.fetcher import _parse_roc_datetime

        assert _parse_roc_datetime("114/13/01") is None

    def test_invalid_day_returns_none(self) -> None:
        from g0vmcp.ingestion.fetcher import _parse_roc_datetime

        assert _parse_roc_datetime("114/02/30") is None

    def test_valid_date_still_works(self) -> None:
        from g0vmcp.ingestion.fetcher import _parse_roc_datetime

        dt = _parse_roc_datetime("114/01/20 14:30")
        assert dt is not None
        assert dt.year == 2025
        assert dt.month == 1
        assert dt.day == 20


class TestGateUrlPathValidation:
    """Verify Cloudflare gate paths cannot redirect requests off-site."""

    @pytest.mark.parametrize(
        "path",
        [
            "https://evil.com/steal",
            "//evil.com/tps/validate/check",
            "/tps/validate/check@evil.com",
        ],
    )
    def test_suspicious_path_rejected(self, path: str) -> None:
        from g0vmcp.ingestion.cf_http import CloudflareAwareHttpGetter

        calls: list[str] = []

        class FakeOpener:
            def open(self, req, timeout=None):
                calls.append(req.full_url)
                raise AssertionError("suspicious gate path must not be requested")

        getter = CloudflareAwareHttpGetter(
            opener=FakeOpener(), sleep=lambda _: None
        )
        html = f'<input id="url" value="{path}"/>'
        assert getter._pass_gate(html) is False
        assert calls == []

    def test_valid_gate_path_accepted(self) -> None:
        from g0vmcp.ingestion.cf_http import CloudflareAwareHttpGetter

        calls: list[str] = []

        class FakeResponse:
            def read(self) -> bytes:
                return b"ok"

            def __enter__(self):
                return self

            def __exit__(self, *args) -> None:
                return None

        class FakeOpener:
            def open(self, req, timeout=None):
                calls.append(req.full_url)
                return FakeResponse()

        getter = CloudflareAwareHttpGetter(
            opener=FakeOpener(), sleep=lambda _: None
        )
        html = '<input id="url" value="/tps/validate/check?token=abc"/>'
        assert getter._pass_gate(html) is True
        assert any("/tps/validate/check" in call for call in calls)


class TestFetchViaSearchHrefValidation:
    """Verify tpam hrefs from search results are validated."""

    async def test_suspicious_href_rejected(self) -> None:
        from g0vmcp.ingestion.fetcher import PccHttpFetcher
        from g0vmcp.ingestion.http import Resp

        class FakeHttp:
            async def __call__(self, url: str) -> Resp:
                return Resp(status_code=200, text="", url=url)

            async def post(self, url: str, data: dict) -> Resp:
                html = (
                    "<table><tr>"
                    "<td>TEST-001</td>"
                    '<td><a href="//evil.com/tpam/page">link</a></td>'
                    "</tr></table>"
                )
                return Resp(status_code=200, text=html, url=url)

        fetcher = PccHttpFetcher(FakeHttp())
        with pytest.raises(RuntimeError, match="unexpected detail href"):
            await fetcher.fetch_detail("TEST-001", None)


class TestSSEPortValidation:
    """Verify SSE port rejects values outside the TCP port range."""

    def test_port_zero_rejected(self, monkeypatch) -> None:
        from g0vmcp.mcp_server import __main__ as entrypoint
        import g0vmcp.repository as repository

        monkeypatch.setenv("G0VMCP_TRANSPORT", "sse")
        monkeypatch.setenv("G0VMCP_PORT", "0")
        monkeypatch.setenv("G0VMCP_DB", "test-port-validation.db")
        monkeypatch.setattr(
            repository,
            "build_repositories",
            lambda _: (FakeTenderRepo(), FakeVendorRepo()),
        )
        monkeypatch.setattr(entrypoint, "build_mcp", lambda _: object())
        with pytest.raises(ValueError, match="1-65535"):
            entrypoint.main()

    def test_port_too_high_rejected(self, monkeypatch) -> None:
        from g0vmcp.mcp_server import __main__ as entrypoint
        import g0vmcp.repository as repository

        monkeypatch.setenv("G0VMCP_TRANSPORT", "sse")
        monkeypatch.setenv("G0VMCP_PORT", "70000")
        monkeypatch.setenv("G0VMCP_DB", "test-port-validation.db")
        monkeypatch.setattr(
            repository,
            "build_repositories",
            lambda _: (FakeTenderRepo(), FakeVendorRepo()),
        )
        monkeypatch.setattr(entrypoint, "build_mcp", lambda _: object())
        with pytest.raises(ValueError, match="1-65535"):
            entrypoint.main()


class TestFastMcpDependencyFloor:
    """Verify pyproject.toml's fastmcp floor excludes known-vulnerable releases.

    fastmcp < 3.2.0 is affected by CVE-2026-32871 (critical, CVSS 9.8 —
    unescaped path-parameter substitution in the OpenAPI provider's
    buildurl() enabling SSRF/path traversal) and requires >= 3.2.0 for the
    related OAuth confused-deputy / credential-forwarding fixes. This repo's
    server.py builds tools directly (no OpenAPI-from-spec, no OAuth
    provider), so the vulnerable code paths are not reachable today — but
    the dependency floor should not silently permit installing a
    known-vulnerable version in a future/fresh environment.
    """

    def test_pyproject_floor_excludes_vulnerable_fastmcp(self) -> None:
        import re
        from pathlib import Path

        from packaging.requirements import Requirement
        from packaging.version import Version

        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        m = re.search(r'"(fastmcp[^"]*)"', text)
        assert m, "fastmcp dependency spec not found in pyproject.toml"
        req = Requirement(m.group(1))
        # Every version satisfying the declared specifier must be >= 3.2.0.
        assert not req.specifier.contains(Version("3.1.0")), (
            "fastmcp floor permits versions vulnerable to CVE-2026-32871 "
            "(SSRF/path traversal, fixed in 3.2.0)"
        )
        assert req.specifier.contains(Version("3.2.0"))

    def test_installed_fastmcp_meets_floor(self) -> None:
        from importlib.metadata import version

        from packaging.version import Version

        assert Version(version("fastmcp")) >= Version("3.2.0")


class TestDataDirPermissions:
    """Verify the local data directory is created with restricted permissions."""

    def test_resolve_db_creates_restricted_dir(self, tmp_path, monkeypatch) -> None:
        import os

        if os.name == "nt":
            pytest.skip("POSIX directory mode bits are not portable to Windows")
        from g0vmcp.mcp_server.__main__ import _resolve_db_path

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.delenv("G0VMCP_DB", raising=False)
        _resolve_db_path()
        data_dir = tmp_path / ".g0vmcp"
        assert data_dir.stat().st_mode & 0o777 == 0o700


# ── 11. Outbound URL Guard (CWE-918 / CWE-22) ────────────────────────────────


class TestOutboundUrlGuard:
    """urllib 的 build_opener() 預設帶 FileHandler/FTPHandler/DataHandler。

    任何非 https://web.pcc.gov.tw 的 URL 走到 opener 都會被真的開啟 ——
    file:///etc/passwd 會被讀回來當 HTML。護欄把 sink 本身關掉。
    """

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://evil.example.com/x",
            "data:text/html,<b>x</b>",
            "http://web.pcc.gov.tw/tps/x",          # 明文
            "https://evil.example.com/tps/x",       # 非 PCC host
            "https://web.pcc.gov.tw@evil.example.com/x",  # userinfo 混淆
        ],
    )
    def test_rejected(self, url: str) -> None:
        from g0vmcp.ingestion.url_guard import UnsafeUrlError, assert_pcc_url

        with pytest.raises(UnsafeUrlError):
            assert_pcc_url(url)

    def test_pcc_https_url_accepted(self) -> None:
        from g0vmcp.ingestion.url_guard import assert_pcc_url

        url = "https://web.pcc.gov.tw/tps/QueryTender/query/searchTenderDetail?pk=1"
        assert assert_pcc_url(url) == url

    async def test_getter_refuses_file_scheme(self, tmp_path) -> None:
        """回歸:CloudflareAwareHttpGetter 先前會讀回本機檔案內容。"""
        from g0vmcp.ingestion.cf_http import CloudflareAwareHttpGetter
        from g0vmcp.ingestion.url_guard import UnsafeUrlError

        secret = tmp_path / "secret.txt"
        secret.write_text("SENSITIVE", encoding="utf-8")
        getter = CloudflareAwareHttpGetter(sleep=lambda _: None)
        with pytest.raises(UnsafeUrlError):
            await getter(secret.as_uri())


# ── 12. Gate Path Validation in enrich_open_date (CWE-918) ───────────────────


class TestGatePathHelper:
    """`_BASE + path` 是字串串接:path 若含 @ 或 // 就能換掉真正的 host。"""

    @pytest.mark.parametrize(
        "path",
        [
            "@evil.example.com/steal",
            "//evil.example.com/x",
            "https://evil.example.com/steal",
            "/tps/validate/check@evil.example.com",
        ],
    )
    def test_unsafe_gate_path_rejected(self, path: str) -> None:
        from g0vmcp.ingestion.url_guard import safe_gate_path

        assert safe_gate_path(path) is None

    def test_valid_gate_path_accepted(self) -> None:
        from g0vmcp.ingestion.url_guard import safe_gate_path

        assert safe_gate_path("/tps/validate/check?token=abc") == (
            "/tps/validate/check?token=abc"
        )

    def test_enrich_script_pass_gate_rejects_userinfo_path(self, monkeypatch) -> None:
        """回歸:cf_http 已擋,但 scripts/enrich_open_date.py 先前漏掉同一檢查。"""
        import importlib

        enrich = importlib.import_module("enrich_open_date")
        calls: list[str] = []
        monkeypatch.setattr(enrich, "_raw_get", lambda url: calls.append(url))
        monkeypatch.setattr(enrich.time, "sleep", lambda _: None)

        html = '<input id="url" value="@evil.example.com/steal"/>'
        assert enrich._pass_gate(html) is False
        assert calls == []

    def test_enrich_script_pass_gate_accepts_relative_path(self, monkeypatch) -> None:
        import importlib

        enrich = importlib.import_module("enrich_open_date")
        calls: list[str] = []
        monkeypatch.setattr(enrich, "_raw_get", lambda url: calls.append(url))
        monkeypatch.setattr(enrich.time, "sleep", lambda _: None)

        html = '<input id="url" value="/tps/validate/check?token=abc"/>'
        assert enrich._pass_gate(html) is True
        assert calls == ["https://web.pcc.gov.tw/tps/validate/check?token=abc"]
