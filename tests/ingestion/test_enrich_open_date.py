"""enrich_open_date 解析層測試。

固定來源:tests/ingestion/fixtures/live_tender_detail.html
(web.pcc.gov.tw tpam 明細頁實況快照,標案 113TFDA-A-513)。
"""
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("httpx")

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from enrich_open_date import extract_open_fields, parse_detail_fields

_FIXTURE = Path(__file__).parent / "fixtures" / "live_tender_detail.html"


@pytest.fixture
def html() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


def test_parse_detail_fields_reads_td_label_value_pairs(html: str):
    fields = parse_detail_fields(html)
    assert fields["標案案號"] == "113TFDA-A-513"
    assert fields["招標方式"] == "公開招標"


def test_extract_open_date_parses_roc_datetime(html: str):
    fields = extract_open_fields(html)
    # 開標時間 114/05/08 10:00 → 西元 2025-05-08 10:00
    assert fields["open_date"] == datetime(2025, 5, 8, 10, 0)
    # 截止投標 114/05/07 17:00
    assert fields["bid_deadline"] == datetime(2025, 5, 7, 17, 0)


def test_extract_returns_none_open_date_when_absent():
    fields = extract_open_fields("<table><tr><td>機關名稱</td><td>X署</td></tr></table>")
    assert fields["open_date"] is None
