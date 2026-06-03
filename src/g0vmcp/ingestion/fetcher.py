"""PccHttpFetcher — 實作 contracts.PccDetailFetcher。

從 web.pcc.gov.tw 明細頁解析 ParsedDetail,補齊 pcc-tender 缺漏的加值欄位。
HTTP 透過注入的 HttpGetter 取得(DI)→ 正式邏輯絕不直接連 web;測試注入 fake。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional, Sequence
from urllib.parse import parse_qs, urlparse

from selectolax.parser import HTMLParser, Node

from g0vmcp.contracts import (
    AnnouncementType,
    AwardedVendor,
    BlockedError,
    ParsedDetail,
    ProcurementProfile,
    TenderId,
)
from g0vmcp.ingestion.http import HttpGetter

# web.pcc.gov.tw 端點 — 僅作 URL 組裝;實際 I/O 走注入的 getter。
_BASE = "https://web.pcc.gov.tw"
_DETAIL_PATH = "/tps/tender/common/bulletion/readBulletion"
_SEARCH_PATH = "/prkms/tender/common/basic/readTenderBasic"
# readTenderBasic POST 搜尋表單(已實測可定位 tpam 明細頁)
_SEARCH_FORM = {
    "pageSize": "50",
    "firstSearch": "true",
    "searchType": "basic",
    "isBinding": "N",
    "isLogIn": "N",
    "dateType": "isDate",
}

_BLOCKED_STATUS = {403, 429}
_MONEY_RE = re.compile(r"[\d,]+")
# ROC 民國日期時間:114/01/20 14:30 或 114/01/20
_ROC_DT_RE = re.compile(r"(\d{2,3})/(\d{1,2})/(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?")
# 標的分類:「4523 - 資訊處理及週邊設備」→ 取前段純數字碼
_CATEGORY_RE = re.compile(r"(\d{3,})")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _parse_money(text: str) -> Optional[int]:
    m = _MONEY_RE.search(text or "")
    if not m:
        return None
    digits = m.group(0).replace(",", "")
    return int(digits) if digits else None


def _parse_roc_datetime(text: str) -> Optional[datetime]:
    m = _ROC_DT_RE.search(text or "")
    if not m:
        return None
    roc_y, mo, d, hh, mm = m.groups()
    year = int(roc_y) + 1911
    hour = int(hh) if hh is not None else 0
    minute = int(mm) if mm is not None else 0
    return datetime(year, int(mo), int(d), hour, minute)


class PccHttpFetcher:
    """contracts.PccDetailFetcher 的實作。

    Args:
        http: 注入的 async HTTP getter(DI);測試注入 fake。
    """

    def __init__(self, http: HttpGetter) -> None:
        self._http = http

    # ------------------------------------------------------------------
    # org_id 反查
    # ------------------------------------------------------------------
    async def resolve_org_id(self, job_number: str, agency: str) -> Optional[str]:
        """以 job_number + 機關名稱反查 org_id;查不到回 None(呼叫端跳過)。"""
        url = f"{_BASE}{_SEARCH_PATH}?caseNo={job_number}"
        try:
            resp = await self._http(url)
        except BlockedError:
            raise
        if resp.status_code in _BLOCKED_STATUS:
            raise BlockedError(f"blocked while resolving org_id: {resp.status_code}")
        if resp.status_code != 200:
            return None
        return self._extract_org_id(resp.text, job_number)

    @staticmethod
    def _extract_org_id(html: str, job_number: str) -> Optional[str]:
        tree = HTMLParser(html)
        for a in tree.css("a[href]"):
            href = a.attributes.get("href") or ""
            if "orgId" not in href:
                continue
            qs = parse_qs(urlparse(href).query)
            case = qs.get("caseNo", [""])[0]
            org = qs.get("orgId", [""])[0]
            if org and (not case or case == job_number):
                return org
        return None

    # ------------------------------------------------------------------
    # 明細擷取
    # ------------------------------------------------------------------
    async def fetch_detail(
        self, job_number: str, org_id: Optional[str]
    ) -> ParsedDetail:
        # 真實 web.pcc 明細頁需 POST 搜尋 → tpam 連結 → GET(getter 具 post 能力時走此路);
        # 無 post 的注入替身(合成測試)退回 readBulletion 直連。
        if hasattr(self._http, "post"):
            return await self._fetch_via_search(job_number, org_id)
        url = f"{_BASE}{_DETAIL_PATH}?caseNo={job_number}&orgId={org_id or ''}"
        resp = await self._http(url)
        if resp.status_code in _BLOCKED_STATUS:
            raise BlockedError(
                f"blocked while fetching detail {job_number}: {resp.status_code}"
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"unexpected status {resp.status_code} for {job_number}"
            )
        return self._parse_detail(resp.text, job_number, org_id, source_url=url)

    async def _fetch_via_search(
        self, job_number: str, org_id: Optional[str]
    ) -> ParsedDetail:
        """readTenderBasic POST 搜尋 → 解析 tpam 明細頁連結 → GET → 解析(雙模式)。

        tpam 明細頁才含標的分類 CPC 碼;org_id 順帶從搜尋結果反查(拿不到則維持空鍵)。
        """
        resp = await self._http.post(
            f"{_BASE}{_SEARCH_PATH}", {"tenderId": job_number, **_SEARCH_FORM}
        )
        href: Optional[str] = None
        for row in HTMLParser(resp.text).css("tr"):
            if job_number not in row.text():
                continue
            link = row.css_first("a[href*=tpam]")
            if link is not None:
                href = link.attributes.get("href")
                break
        if not href:
            raise RuntimeError(f"no tpam detail link for {job_number}")
        detail_url = f"{_BASE}{href}"
        detail_resp = await self._http(detail_url)
        resolved = org_id or self._extract_org_id(resp.text, job_number)
        return self._parse_detail(
            detail_resp.text, job_number, resolved, source_url=detail_url
        )

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    def _parse_detail(
        self,
        html: str,
        job_number: str,
        org_id: Optional[str],
        *,
        source_url: str,
    ) -> ParsedDetail:
        tree = HTMLParser(html)
        fields = self._extract_fields(tree)

        ann_type = self._detect_ann_type(tree, fields)
        attr, category_code = self._parse_category(fields.get("標的分類", ""))

        procurement = ProcurementProfile(
            attr=attr,
            type=fields.get("招標方式") or None,
            way=fields.get("決標方式") or None,
        )

        detail = ParsedDetail(
            tender_id=TenderId(org_id=org_id or "", job_number=job_number),
            title=fields.get("標案名稱", ""),
            agency=fields.get("機關名稱", ""),
            ann_type=ann_type,
            ann_date=date.today(),
            budget=_parse_money(fields.get("預算金額", "")),
            open_date=_parse_roc_datetime(fields.get("開標時間", "")),
            bid_deadline=_parse_roc_datetime(fields.get("截止投標", "")),
            base_price=_parse_money(fields.get("底價金額", "")),
            bidder_count=self._parse_int(fields.get("投標廠商家數", "")),
            category_code=category_code,
            procurement=procurement,
            source_url=source_url,
            raw=fields,
        )

        if ann_type is AnnouncementType.AWARD:
            detail.award_price = _parse_money(fields.get("總決標金額", ""))
            detail.vendors = self._parse_vendors(tree)

        return detail

    @staticmethod
    def _extract_fields(tree: HTMLParser) -> dict[str, str]:
        """雙模式掃描 → {label: value};首見為準(first-seen wins)。

        合成頁用 <th>label</th><td>value</td>;真實 web.pcc 明細頁則整列皆 <td>,
        label 與 value 為相鄰兩個 td。逐列先試 th/td,該列無 th 才退回 td/td,
        兩種結構同頁可並存。
        """
        fields: dict[str, str] = {}
        for row in tree.css("tr"):
            th = row.css_first("th")
            if th is not None:
                td = row.css_first("td")
                if td is None:
                    continue
                label = _clean(th.text()).rstrip(":：")
                if label and label not in fields:
                    fields[label] = _clean(td.text())
                continue
            # 無 th → td/td 模型:相鄰兩 td 視為 label/value(併入明細頁實戰邏輯)
            cells = row.css("td")
            for i in range(len(cells) - 1):
                label = _clean(cells[i].text()).rstrip(":：")
                if label and label not in fields:
                    fields[label] = _clean(cells[i + 1].text())
        return fields

    @staticmethod
    def _detect_ann_type(tree: HTMLParser, fields: dict[str, str]) -> AnnouncementType:
        title = tree.css_first("title")
        title_text = title.text() if title else ""
        if "決標" in title_text or "總決標金額" in fields or "得標廠商" in fields:
            return AnnouncementType.AWARD
        if "無法決標" in title_text:
            return AnnouncementType.FAILURE
        if "更正" in title_text:
            return AnnouncementType.AMENDMENT
        return AnnouncementType.TENDER

    @staticmethod
    def _parse_category(text: str) -> tuple[Optional[str], Optional[str]]:
        """回傳 (採購性質 attr, category_code)。

        標的分類欄常含「財物類 4523 - 資訊處理…」;attr 取性質詞,code 取數字碼。
        """
        attr: Optional[str] = None
        for candidate in ("工程類", "財物類", "勞務類"):
            if candidate in text:
                attr = candidate
                break
        m = _CATEGORY_RE.search(text)
        code = m.group(1) if m else None
        return attr, code

    @staticmethod
    def _parse_int(text: str) -> Optional[int]:
        m = re.search(r"\d+", text or "")
        return int(m.group(0)) if m else None

    @staticmethod
    def _parse_vendors(tree: HTMLParser) -> list[AwardedVendor]:
        vendors: list[AwardedVendor] = []
        for table in tree.css("table.vendor_table"):
            for row in table.css("tr"):
                cells: Sequence[Node] = row.css("td")
                if len(cells) < 3:
                    continue
                name = _clean(cells[0].text())
                tax_id = _clean(cells[1].text())
                price = _parse_money(cells[2].text())
                if name and tax_id and price is not None:
                    vendors.append(
                        AwardedVendor(tax_id=tax_id, name=name, award_price=price)
                    )
        return vendors
