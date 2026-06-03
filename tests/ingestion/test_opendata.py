"""opendata.py 純函式測試:半月招標 XML 解析 / 下載 URL / showList 解析。"""
from __future__ import annotations

from g0vmcp.ingestion.opendata import (
    AwardRow,
    OpenDataRow,
    award_xml_url,
    parse_award_showlist,
    parse_award_xml,
    parse_tender_xml,
    tender_xml_url,
)

from .conftest import load_fixture


class TestParseTenderXml:
    def test_parses_all_records(self) -> None:
        rows = parse_tender_xml(load_fixture("tender_halfmonth.xml"))
        assert len(rows) == 3
        assert all(isinstance(r, OpenDataRow) for r in rows)

    def test_first_record_fields(self) -> None:
        rows = parse_tender_xml(load_fixture("tender_halfmonth.xml"))
        r = rows[0]
        assert r.ann_date == "2026/03/02"
        assert r.org_name == "衛生福利部疾病管制署"
        assert r.case_no == "CDC-115-IT-001"
        assert r.title == "傳染病監測資訊系統維運案"
        assert r.procurement_type == "公開招標"
        assert r.procurement_attr == "勞務類"

    def test_missing_field_yields_empty_string(self) -> None:
        # 第三筆缺 PROCUREMENT_ATTR
        rows = parse_tender_xml(load_fixture("tender_halfmonth.xml"))
        assert rows[2].procurement_attr == ""
        assert rows[2].org_name == "交通部公路局南區公路新建工程分局"

    def test_empty_list_for_no_records(self) -> None:
        assert parse_tender_xml("<TENDER_LIST></TENDER_LIST>") == []


class TestTenderXmlUrl:
    def test_second_half(self) -> None:
        assert tender_xml_url(2026, 3, 2) == (
            "https://web.pcc.gov.tw/tps/tp/OpenData/downloadFile"
            "?fileName=tender_20260302.xml"
        )

    def test_first_half_zero_padded_month(self) -> None:
        assert tender_xml_url(2026, 1, 1) == (
            "https://web.pcc.gov.tw/tps/tp/OpenData/downloadFile"
            "?fileName=tender_20260101.xml"
        )


class TestParseAwardXml:
    def test_parses_all_records(self) -> None:
        rows = parse_award_xml(load_fixture("award_halfmonth.xml"))
        assert len(rows) == 2
        assert all(isinstance(r, AwardRow) for r in rows)

    def test_first_record_fields_and_single_winner(self) -> None:
        r = parse_award_xml(load_fixture("award_halfmonth.xml"))[0]
        assert r.award_date == "2026/03/16"
        assert r.notice_date == "2026/03/20"
        assert r.org_name == "衛生福利部中央健康保險署"
        assert r.case_no == "NHI-115-IT-001"
        assert r.award_price == "963800000"
        assert r.award_way == "準用最有利標"
        assert len(r.winners) == 1
        assert r.winners[0].startswith("資拓宏宇國際股份有限公司")

    def test_multiple_winners_excludes_not_obtain(self) -> None:
        r = parse_award_xml(load_fixture("award_halfmonth.xml"))[1]
        # 兩個得標廠商,未得標廠商不計入 winners
        assert r.winners == ("得標廠商甲", "得標廠商乙")

    def test_empty_list_for_no_records(self) -> None:
        assert parse_award_xml("<TENDER_LIST></TENDER_LIST>") == []


class TestAwardXmlUrl:
    def test_award_url_same_route_as_tender(self) -> None:
        assert award_xml_url(2026, 3, 2) == (
            "https://web.pcc.gov.tw/tps/tp/OpenData/downloadFile"
            "?fileName=award_20260302.xml"
        )


class TestParseAwardShowlist:
    def test_extracts_filename_to_id_or_filename_map(self) -> None:
        html = (
            '<html><body>'
            '<a href="downloadFile?fileName=award_20260302.xml">下半月決標</a>'
            '<a href="DownloadFile?id=70000016">教學影片</a>'
            '</body></html>'
        )
        result = parse_award_showlist(html)
        # fileName-based award link 應被抽出
        assert "award_20260302.xml" in result
