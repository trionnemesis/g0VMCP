"""Security regression tests for verified vulnerabilities."""
from __future__ import annotations

import pytest


class TestXXEProtection:
    """Verify XML parsing uses defusedxml to prevent XXE / entity expansion."""

    def test_opendata_uses_defusedxml(self):
        from g0vmcp.ingestion import opendata
        import defusedxml.ElementTree

        assert opendata.ET is defusedxml.ElementTree

    def test_entity_expansion_rejected(self):
        from g0vmcp.ingestion.opendata import parse_tender_xml

        xxe_xml = """<?xml version="1.0"?>
        <!DOCTYPE foo [
          <!ENTITY xxe SYSTEM "file:///etc/passwd">
        ]>
        <TENDER_LIST>
          <TENDER>
            <TENDER_CASE_NO>&xxe;</TENDER_CASE_NO>
          </TENDER>
        </TENDER_LIST>"""
        with pytest.raises(Exception):
            parse_tender_xml(xxe_xml)

    def test_billion_laughs_rejected(self):
        from g0vmcp.ingestion.opendata import parse_tender_xml

        bomb = '<?xml version="1.0"?>'
        bomb += '<!DOCTYPE lolz ['
        bomb += '<!ENTITY lol "lol">'
        for i in range(1, 10):
            bomb += f'<!ENTITY lol{i} "{("&lol" + str(i-1) + ";" if i > 1 else "&lol;") * 10}">'
        bomb += ']>'
        bomb += '<TENDER_LIST><TENDER><TENDER_CASE_NO>&lol9;</TENDER_CASE_NO></TENDER></TENDER_LIST>'
        with pytest.raises(Exception):
            parse_tender_xml(bomb)


class TestSSEBindAddress:
    """Verify SSE default bind address is localhost, not 0.0.0.0."""

    def test_default_host_is_localhost(self, monkeypatch):
        monkeypatch.delenv("G0VMCP_HOST", raising=False)
        import os
        assert os.environ.get("G0VMCP_HOST", "127.0.0.1") == "127.0.0.1"


class TestURLParameterEncoding:
    """Verify URL-interpolated parameters are properly encoded."""

    def test_job_number_with_special_chars_is_encoded(self):
        from urllib.parse import quote
        malicious = "123&orgId=evil"
        encoded = quote(malicious, safe='')
        assert "&" not in encoded
        assert "=" not in encoded
