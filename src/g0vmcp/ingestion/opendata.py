"""半月公開資料(open data)解析純函式,零網路 I/O。

招標半月檔 tender_YYYYMMNN.xml 由 PCC OpenData 提供,根節點 <TENDER_LIST>,
每筆 <TENDER> 含 6 欄位。決標檔 award_*.xml 走同一 downloadFile?fileName= 路由
(spike 已驗證),其 showList 頁面連結由 parse_award_showlist 抽出。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

_DOWNLOAD_BASE = "https://web.pcc.gov.tw/tps/tp/OpenData/downloadFile"


@dataclass(frozen=True)
class OpenDataRow:
    """半月招標 XML 單筆。"""

    ann_date: str  # TENDER_SPDT,格式 2026/04/20
    org_name: str
    case_no: str
    title: str
    procurement_type: str
    procurement_attr: str


@dataclass(frozen=True)
class AwardRow:
    """半月決標 XML 單筆。winners 為得標廠商名(無統編,與 pcc companies 一致)。"""

    award_date: str       # AWARD_DATE
    notice_date: str      # AWARD_NOTICE_DATE
    org_name: str
    case_no: str
    title: str
    procurement_type: str
    procurement_attr: str
    award_way: str        # TENDER_AWARD_WAY
    award_price: str      # TENDER_AWARD_PRICE(字串,可能空)
    winners: tuple[str, ...]  # BIDDER_LIST 下 BIDDER_SUPP_NAME(可多筆/空)


def _text(node: ET.Element, tag: str) -> str:
    """缺欄位 / 空內容一律回空字串(容錯)。"""
    child = node.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def parse_tender_xml(xml: str) -> list[OpenDataRow]:
    """解析半月招標 XML 為 OpenDataRow 串列。缺欄位以空字串填充。"""
    root = ET.fromstring(xml)
    rows: list[OpenDataRow] = []
    for t in root.iter("TENDER"):
        rows.append(
            OpenDataRow(
                ann_date=_text(t, "TENDER_SPDT"),
                org_name=_text(t, "TENDER_ORG_NAME"),
                case_no=_text(t, "TENDER_CASE_NO"),
                title=_text(t, "TENDER_NAME"),
                procurement_type=_text(t, "PROCUREMENT_TYPE"),
                procurement_attr=_text(t, "PROCUREMENT_ATTR"),
            )
        )
    return rows


def parse_award_xml(xml: str) -> list[AwardRow]:
    """解析半月決標 XML。得標廠商在巢狀 <BIDDER_LIST>/<BIDDER_SUPP_NAME>(可多筆/空)。"""
    root = ET.fromstring(xml)
    rows: list[AwardRow] = []
    for t in root.iter("TENDER"):
        bl = t.find("BIDDER_LIST")
        winners: tuple[str, ...] = ()
        if bl is not None:
            winners = tuple(
                e.text.strip()
                for e in bl.iter("BIDDER_SUPP_NAME")
                if e.text and e.text.strip()
            )
        rows.append(
            AwardRow(
                award_date=_text(t, "AWARD_DATE"),
                notice_date=_text(t, "AWARD_NOTICE_DATE"),
                org_name=_text(t, "TENDER_ORG_NAME"),
                case_no=_text(t, "TENDER_CASE_NO"),
                title=_text(t, "TENDER_NAME"),
                procurement_type=_text(t, "PROCUREMENT_TYPE"),
                procurement_attr=_text(t, "PROCUREMENT_ATTR"),
                award_way=_text(t, "TENDER_AWARD_WAY"),
                award_price=_text(t, "TENDER_AWARD_PRICE"),
                winners=winners,
            )
        )
    return rows


def _halfmonth_url(prefix: str, year: int, month: int, half: int) -> str:
    """組半月檔下載 URL。half=1 → NN=01(上半月),half=2 → NN=02(下半月)。"""
    file_name = f"{prefix}_{year:04d}{month:02d}{half:02d}.xml"
    return f"{_DOWNLOAD_BASE}?fileName={file_name}"


def tender_xml_url(year: int, month: int, half: int) -> str:
    """半月招標檔下載 URL。"""
    return _halfmonth_url("tender", year, month, half)


def award_xml_url(year: int, month: int, half: int) -> str:
    """半月決標檔下載 URL(spike 確認與招標同 downloadFile?fileName= 路由)。"""
    return _halfmonth_url("award", year, month, half)


# showList 頁面決標連結:href="downloadFile?fileName=award_YYYYMMNN.xml"
# (大寫 D 的 DownloadFile?id= 為無關的教學影片下載,排除)
_FILENAME_HREF = re.compile(r"downloadFile\?fileName=([^\"'&<>\s]+)")


def parse_award_showlist(html: str) -> dict[str, str]:
    """從 showList 頁 HTML 抽出決標檔下載對應 {fileName: fileName}。

    spike 結論:決標 XML 與招標同走 downloadFile?fileName= 路由,可直接下載,
    無需 id。故回傳以 fileName 為 key/value 的對應(供主線組 URL 用)。
    大寫 D 的 DownloadFile?id= 連結為教學影片,不納入。
    """
    result: dict[str, str] = {}
    for m in _FILENAME_HREF.finditer(html):
        file_name = m.group(1)
        result[file_name] = file_name
    return result
