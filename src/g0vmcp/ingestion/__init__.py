"""任務① 擷取層 Ingestion。

從 web.pcc.gov.tw 明細頁擷取 pcc-tender 缺漏的加值欄位。
對外實作 contracts.PccDetailFetcher Protocol。
"""
from g0vmcp.ingestion.backoff import ExponentialBackoff
from g0vmcp.ingestion.cf_http import CloudflareAwareHttpGetter
from g0vmcp.ingestion.fetch_log import FetchLog
from g0vmcp.ingestion.fetcher import PccHttpFetcher
from g0vmcp.ingestion.http import HttpGetter, Resp
from g0vmcp.ingestion.mappers import detail_to_tender
from g0vmcp.ingestion.opendata import (
    OpenDataRow,
    parse_award_showlist,
    parse_tender_xml,
    tender_xml_url,
)
from g0vmcp.ingestion.scope import is_it_cpc, is_mohw, keyword_prescreen

__all__ = [
    "PccHttpFetcher",
    "FetchLog",
    "ExponentialBackoff",
    "HttpGetter",
    "Resp",
    "OpenDataRow",
    "parse_tender_xml",
    "tender_xml_url",
    "parse_award_showlist",
    "is_mohw",
    "is_it_cpc",
    "keyword_prescreen",
    "CloudflareAwareHttpGetter",
    "detail_to_tender",
]
