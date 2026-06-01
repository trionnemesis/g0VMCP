"""任務① 擷取層 Ingestion。

從 web.pcc.gov.tw 明細頁擷取 pcc-tender 缺漏的加值欄位。
對外實作 contracts.PccDetailFetcher Protocol。
"""
from g0vmcp.ingestion.backoff import ExponentialBackoff
from g0vmcp.ingestion.fetch_log import FetchLog
from g0vmcp.ingestion.fetcher import PccHttpFetcher
from g0vmcp.ingestion.http import HttpGetter, Resp

__all__ = [
    "PccHttpFetcher",
    "FetchLog",
    "ExponentialBackoff",
    "HttpGetter",
    "Resp",
]
