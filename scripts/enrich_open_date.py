#!/usr/bin/env python3
"""回填 open_date / bid_deadline 等「明細頁加值欄位」到 g0vmcp.db。

背景：pcc-tender 開放資料(含官方半月 OpenData XML)只有 21 欄,**不含開標時間**;
開標時間僅存在於 web.pcc.gov.tw 個別標案明細頁。本腳本對「有招標公告」的標案
(公開招標 / 經公開評選之限制性招標)做：
  tenderId 查詢 → 取招標公告 tpam pk → 抓明細頁 → 解析開標時間/截止投標/底價/家數
  → 以 repository.save() upsert 回寫(重用既測寫入路徑)。

限制性招標(未經公開評選)無公開招標公告,本就無開標時間,open_date 維持 NULL。

使用方式: python3 scripts/enrich_open_date.py
"""
from __future__ import annotations

import asyncio
import html as _htmllib
import http.cookiejar
import re
import sys
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from selectolax.parser import HTMLParser

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from g0vmcp.contracts import Money
from g0vmcp.ingestion.fetcher import _parse_money, _parse_roc_datetime
from g0vmcp.repository import build_repositories

DB_PATH = str(Path(__file__).resolve().parents[1] / "g0vmcp.db")
_BASE = "https://web.pcc.gov.tw"
_SEARCH = f"{_BASE}/prkms/tender/common/basic/readTenderBasic"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

MAX_PER_RUN = 30  # 每次執行最多補爬數量，避免觸發速率限制


class _RateLimitedError(Exception):
    """PCC 計時閘門重試耗盡 → 觸發 BLOCKED 退避。"""


def parse_detail_fields(html: str) -> dict[str, str]:
    """明細頁 label/value 皆為 <td> → {label: value}(首見為準)。

    與 fetcher._extract_fields(th/td) 不同:tpam 明細頁 label 也用 td。
    """
    tree = HTMLParser(html)
    fields: dict[str, str] = {}
    for row in tree.css("tr"):
        cells = row.css("td")
        for i, cell in enumerate(cells):
            if i + 1 >= len(cells):
                continue
            label = " ".join(cell.text().split())
            if label and label not in fields:
                fields[label] = " ".join(cells[i + 1].text().split())
    return fields


def extract_open_fields(html: str) -> dict:
    """從明細頁抽出加值欄位;開標時間缺漏回 open_date=None。"""
    f = parse_detail_fields(html)
    return {
        "open_date": _parse_roc_datetime(f.get("開標時間", "")),
        "bid_deadline": _parse_roc_datetime(f.get("截止投標", "")),
        "budget": _parse_money(f.get("預算金額", "")),
        "base_price": _parse_money(f.get("底價金額", "")),
        "bidder_count": _parse_int(f.get("投標廠商家數", "")),
    }


def _parse_int(text: str) -> Optional[int]:
    digits = "".join(ch for ch in (text or "") if ch.isdigit())
    return int(digits) if digits else None


# 共用 cookie jar:PCC 對密集請求會回計時閘門頁(/tps/validate),
# 通過後設驗證 cookie;沿用同一 opener 才能保留。
_OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
)
_GATE_RE = re.compile(r'id="url"[^>]*value="([^"]*)"')
_GATE_MAX_RETRY = 3


def _raw_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with _OPENER.open(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def _is_gate(html: str) -> bool:
    return "/tps/validate/check" in html and "開標時間" not in html


def _pass_gate(html: str) -> bool:
    """偵測到計時閘門 → 等候後請求 validate/check 取得驗證 cookie。回是否已通過。"""
    m = _GATE_RE.search(html)
    if not m:
        return False
    time.sleep(3)  # 尊重速率限制的計時等候
    _raw_get(_BASE + _htmllib.unescape(m.group(1)))
    return True


def _http_get(url: str) -> str:
    html = _raw_get(url)
    for _ in range(_GATE_MAX_RETRY):
        if not _is_gate(html):
            return html
        if not _pass_gate(html):
            # 找不到驗證 URL → PCC 結構異常,非速率封鎖,直接回傳
            return html
        html = _raw_get(url)
    # 重試耗盡仍是閘門頁 → 確定被速率封鎖
    raise _RateLimitedError(f"rate-limited after {_GATE_MAX_RETRY} gate retries: {url}")


def _http_post(url: str, data: dict) -> str:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"User-Agent": _UA, "Content-Type": "application/x-www-form-urlencoded"},
    )
    with _OPENER.open(req, timeout=40) as r:
        html = r.read().decode("utf-8", "replace")
    for _ in range(_GATE_MAX_RETRY):
        if not _is_gate(html):
            return html
        if not _pass_gate(html):
            break
        with _OPENER.open(req, timeout=40) as r:
            html = r.read().decode("utf-8", "replace")
    return html


def fetch_open_date(job_number: str) -> Optional[dict]:
    """查 job_number 的招標公告明細,回傳最新一筆含開標時間的加值欄位。"""
    html = _http_post(
        _SEARCH,
        {
            "tenderId": job_number,
            "pageSize": "50",
            "firstSearch": "true",
            "searchType": "basic",
            "isBinding": "N",
            "isLogIn": "N",
            "dateType": "isDate",
        },
    )
    tree = HTMLParser(html)

    # 搜尋結果依公告日期遞減;取第一筆「招標」類且含開標時間者 = 最新(含更正)
    for row in tree.css("tr"):
        if job_number not in row.text():
            continue
        cells = [c.text() for c in row.css("td")]
        if not any("招標" in c or "徵求" in c for c in cells):
            continue
        link = row.css_first("a[href*=tpam]")
        href = link.attributes.get("href") if link else None
        if not href:
            continue
        fields = extract_open_fields(_http_get(f"{_BASE}{href}"))
        if fields["open_date"] is not None:
            return fields
        time.sleep(random.uniform(0.3, 1.2))  # jitter 禮貌間隔，避免固定節奏觸發速率限制
    return None


async def _fetch_log_upsert(
    conn,
    job_number: str,
    status: str,
    error_msg: Optional[str] = None,
    retry_hours: int = 0,
) -> None:
    """寫入/更新 fetch_log 一筆。retry_hours>0 時計算 retry_after。"""
    retry_after: Optional[str] = None
    if retry_hours > 0:
        from datetime import timedelta
        retry_after = (datetime.utcnow() + timedelta(hours=retry_hours)).isoformat()
    await conn.execute(
        """
        INSERT INTO fetch_log (job_number, status, last_attempt, error_msg, retry_after)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(job_number) DO UPDATE SET
            status=excluded.status,
            last_attempt=excluded.last_attempt,
            error_msg=excluded.error_msg,
            retry_after=excluded.retry_after
        """,
        (job_number, status, datetime.utcnow().isoformat(), error_msg, retry_after),
    )
    await conn.commit()


async def _is_blocked(conn, job_number: str) -> bool:
    """BLOCKED 且 retry_after 未到 → True(跳過本次)。"""
    cur = await conn.execute(
        "SELECT status, retry_after FROM fetch_log WHERE job_number = ?",
        (job_number,),
    )
    row = await cur.fetchone()
    if row is None:
        return False
    status, retry_after = row[0], row[1]
    if status != "BLOCKED":
        return False
    if retry_after is None:
        return True
    return datetime.utcnow().isoformat() < retry_after


async def main(tender_repo) -> None:
    # 補爬 open_date IS NULL 的公開招標；BLOCKED 案透過 fetch_log 子查詢排除
    cur = await tender_repo._conn.execute(
        """
        SELECT t.job_number FROM tenders t
        LEFT JOIN fetch_log fl ON fl.job_number = t.job_number
        WHERE t.open_date IS NULL
          AND (
            t.procurement_type NOT LIKE '%限制性招標(未經公開評選%'
            OR t.procurement_type IS NULL
          )
          AND NOT (
            fl.status = 'BLOCKED'
            AND fl.retry_after IS NOT NULL
            AND fl.retry_after > strftime('%Y-%m-%dT%H:%M:%S', 'now')
          )
        LIMIT ?
        """,
        (MAX_PER_RUN,),
    )
    rows = await cur.fetchall()
    job_numbers = [row[0] for row in rows]

    if not job_numbers:
        print("所有公開招標標案 open_date 已補齊（或全為 BLOCKED），無需爬取。")
        return

    print(f"待補爬標案：{len(job_numbers)} 筆（上限 {MAX_PER_RUN}）")
    updated, skipped = 0, []

    for job in job_numbers:
        tender = await tender_repo.get(f":{job}")
        if tender is None:
            skipped.append((job, "not in DB"))
            continue
        if tender.open_date is not None:
            skipped.append((job, "已有 open_date"))
            continue

        try:
            fields = fetch_open_date(job)
        except _RateLimitedError as exc:
            # PCC 閘門重試耗盡 → BLOCKED，4 小時後再試
            await _fetch_log_upsert(tender_repo._conn, job, "BLOCKED", str(exc), retry_hours=4)
            skipped.append((job, f"封鎖(BLOCKED 4h): {exc}"))
            print(f"  ⛔ {job}: 速率封鎖，退避 4 小時")
            break  # 停止本次整批，避免繼續觸發封鎖
        except (urllib.error.URLError, OSError) as exc:
            await _fetch_log_upsert(tender_repo._conn, job, "FAILED", str(exc))
            skipped.append((job, f"網路錯誤: {exc}"))
            continue
        if fields is None:
            await _fetch_log_upsert(tender_repo._conn, job, "FAILED", "明細頁無開標時間")
            skipped.append((job, "明細頁無開標時間"))
            continue

        tender.open_date = fields["open_date"]
        tender.bid_deadline = fields["bid_deadline"]
        if fields["budget"] is not None and tender.budget is None:
            tender.budget = Money(fields["budget"])
        if fields["base_price"] is not None and tender.base_price is None:
            tender.base_price = Money(fields["base_price"])
        if fields["bidder_count"] is not None:
            tender.bidder_count = fields["bidder_count"]

        await tender_repo.save(tender)
        await _fetch_log_upsert(tender_repo._conn, job, "SUCCESS")
        updated += 1
        print(
            f"  ✓ {job:<16} open_date={fields['open_date']:%Y-%m-%d %H:%M} "
            f"bid_deadline={_fmt(fields['bid_deadline'])}"
        )
        time.sleep(2.0)

    print(f"\n更新 {updated} 筆;略過 {len(skipped)} 筆")
    for job, why in skipped:
        print(f"  - {job}: {why}")


def _fmt(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"


if __name__ == "__main__":
    tender_repo, _ = build_repositories(DB_PATH)
    asyncio.run(main(tender_repo))
