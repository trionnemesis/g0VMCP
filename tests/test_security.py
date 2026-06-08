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


# ── 1. XML Entity Expansion (CWE-776) ────────────────────────────────────────


class TestXmlEntityExpansion:
    """Verify that XML parsing rejects DTD declarations."""

    def test_billion_laughs_rejected(self) -> None:
        xml = (
            '<?xml version="1.0"?>'
            "<!DOCTYPE lolz ["
            '<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
            "]>"
            "<TENDER_LIST>&lol2;</TENDER_LIST>"
        )
        with pytest.raises(ValueError, match="DTD"):
            parse_tender_xml(xml)

    def test_xxe_rejected(self) -> None:
        xml = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            "<TENDER_LIST>&xxe;</TENDER_LIST>"
        )
        with pytest.raises(ValueError, match="DTD"):
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
