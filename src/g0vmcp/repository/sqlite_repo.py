"""aiosqlite 實作 contracts.TenderRepository / VendorRepository。

DTO ↔ row 轉換集中在此。announcements 子表隨 tender 一同 save(覆寫式)。
vendor_awards 由決標公告投影寫入(save 時自動展開 AWARD 公告 payload 內的 vendors)。
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any, Optional, Sequence

import aiosqlite

from g0vmcp.contracts import (
    Announcement,
    AnnouncementType,
    AwardedVendor,
    Category,
    Money,
    ProcurementProfile,
    Tender,
    TenderId,
    TenderState,
    VendorAward,
)


# --------------------------------------------------------------------------
# 序列化 helpers
# --------------------------------------------------------------------------
def _dt_to_str(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _date_to_str(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _str_to_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _str_to_date(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value else None


# --------------------------------------------------------------------------
# Tender Repository
# --------------------------------------------------------------------------
class SqliteTenderRepository:
    """contracts.TenderRepository 的 aiosqlite 實作。"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = aiosqlite.Row

    async def get(self, tender_id: str) -> Optional[Tender]:
        cur = await self._conn.execute(
            "SELECT * FROM tenders WHERE tender_id = ?", (tender_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        anns = await self._load_announcements(tender_id)
        return self._row_to_tender(row, anns)

    async def save(self, tender: Tender) -> None:
        tid = str(tender.tender_id)
        await self._conn.execute(
            """
            INSERT INTO tenders (
                tender_id, org_id, job_number, agency, title, lifecycle_state,
                budget, budget_currency, open_date, bid_deadline,
                base_price, base_price_currency, bidder_count,
                procurement_attr, procurement_type, award_way,
                category_code, category_name, domain_tag, category_method
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(tender_id) DO UPDATE SET
                agency=excluded.agency,
                title=excluded.title,
                lifecycle_state=excluded.lifecycle_state,
                budget=excluded.budget,
                budget_currency=excluded.budget_currency,
                open_date=excluded.open_date,
                bid_deadline=excluded.bid_deadline,
                base_price=excluded.base_price,
                base_price_currency=excluded.base_price_currency,
                bidder_count=excluded.bidder_count,
                procurement_attr=excluded.procurement_attr,
                procurement_type=excluded.procurement_type,
                award_way=excluded.award_way,
                category_code=excluded.category_code,
                category_name=excluded.category_name,
                domain_tag=excluded.domain_tag,
                category_method=excluded.category_method
            """,
            (
                tid,
                tender.tender_id.org_id,
                tender.tender_id.job_number,
                tender.agency,
                tender.title,
                tender.state.value,
                tender.budget.amount if tender.budget else None,
                tender.budget.currency if tender.budget else None,
                _dt_to_str(tender.open_date),
                _dt_to_str(tender.bid_deadline),
                tender.base_price.amount if tender.base_price else None,
                tender.base_price.currency if tender.base_price else None,
                tender.bidder_count,
                tender.procurement.attr,
                tender.procurement.type,
                tender.procurement.way,
                tender.category.code if tender.category else None,
                tender.category.name if tender.category else None,
                tender.category.domain_tag if tender.category else None,
                tender.category.method if tender.category else None,
            ),
        )

        # announcements 覆寫式同步
        await self._conn.execute(
            "DELETE FROM announcements WHERE tender_id = ?", (tid,)
        )
        for ann in tender.announcements:
            await self._conn.execute(
                """
                INSERT INTO announcements (
                    tender_id, ann_type, tender_seq, ann_date,
                    notice_date, payload, source_url
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    tid,
                    ann.ann_type.value,
                    ann.tender_seq,
                    _date_to_str(ann.ann_date),
                    _date_to_str(ann.notice_date),
                    json.dumps(ann.payload, default=str, ensure_ascii=False),
                    ann.source_url,
                ),
            )

        await self._project_vendor_awards(tender)
        await self._conn.commit()

    async def search(
        self,
        *,
        keyword: Optional[str] = None,
        domain_tag: Optional[str] = None,
        agency: Optional[str] = None,
        budget_min: Optional[int] = None,
        budget_max: Optional[int] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        limit: int = 50,
    ) -> Sequence[Tender]:
        clauses: list[str] = []
        params: list[Any] = []

        if keyword:
            clauses.append("(t.title LIKE ? OR t.agency LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        if domain_tag:
            clauses.append("t.domain_tag = ?")
            params.append(domain_tag)
        if agency:
            clauses.append("t.agency LIKE ?")
            params.append(f"%{agency}%")
        if budget_min is not None:
            clauses.append("t.budget >= ?")
            params.append(budget_min)
        if budget_max is not None:
            clauses.append("t.budget <= ?")
            params.append(budget_max)

        # 日期過濾:依該標案最早公告日(招標公告日)落在區間
        if date_from is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM announcements a WHERE a.tender_id = t.tender_id "
                "AND a.ann_date >= ?)"
            )
            params.append(_date_to_str(date_from))
        if date_to is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM announcements a WHERE a.tender_id = t.tender_id "
                "AND a.ann_date <= ?)"
            )
            params.append(_date_to_str(date_to))

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT t.* FROM tenders t {where} ORDER BY t.tender_id LIMIT ?"
        params.append(limit)

        cur = await self._conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
        results: list[Tender] = []
        for row in rows:
            anns = await self._load_announcements(row["tender_id"])
            results.append(self._row_to_tender(row, anns))
        return results

    # ----- 內部 -----
    async def _load_announcements(self, tender_id: str) -> list[Announcement]:
        cur = await self._conn.execute(
            "SELECT * FROM announcements WHERE tender_id = ? ORDER BY ann_date",
            (tender_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_announcement(r) for r in rows]

    async def _project_vendor_awards(self, tender: Tender) -> None:
        """從決標公告 payload 的 vendors 投影 vendor_awards(冪等:先清本標案)。"""
        tid = str(tender.tender_id)
        await self._conn.execute(
            "DELETE FROM vendor_awards WHERE tender_id = ?", (tid,)
        )
        for ann in tender.announcements:
            if ann.ann_type is not AnnouncementType.AWARD:
                continue
            for v in ann.payload.get("vendors", []):
                tax_id = v["tax_id"]
                name = v["name"]
                award_price = int(v["award_price"])
                await self._conn.execute(
                    """
                    INSERT INTO vendors (tax_id, name)
                    VALUES (?, ?)
                    ON CONFLICT(tax_id) DO UPDATE SET name=excluded.name
                    """,
                    (tax_id, name),
                )
                await self._conn.execute(
                    """
                    INSERT INTO vendor_awards (
                        id, vendor_tax_id, vendor_name, tender_id,
                        award_price, award_currency, awarded_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        str(uuid.uuid4()),
                        tax_id,
                        name,
                        tid,
                        award_price,
                        "TWD",
                        _date_to_str(ann.ann_date),
                    ),
                )

    @staticmethod
    def _row_to_tender(row: aiosqlite.Row, anns: list[Announcement]) -> Tender:
        category = None
        if row["category_code"] is not None or row["domain_tag"] is not None:
            category = Category(
                code=row["category_code"] or "",
                name=row["category_name"] or "",
                domain_tag=row["domain_tag"] or "",
                method=row["category_method"] or "official_code",
            )
        return Tender(
            tender_id=TenderId(org_id=row["org_id"], job_number=row["job_number"]),
            agency=row["agency"],
            title=row["title"],
            state=TenderState(row["lifecycle_state"]),
            announcements=anns,
            budget=(
                Money(amount=row["budget"], currency=row["budget_currency"] or "TWD")
                if row["budget"] is not None
                else None
            ),
            open_date=_str_to_dt(row["open_date"]),
            bid_deadline=_str_to_dt(row["bid_deadline"]),
            base_price=(
                Money(
                    amount=row["base_price"],
                    currency=row["base_price_currency"] or "TWD",
                )
                if row["base_price"] is not None
                else None
            ),
            bidder_count=row["bidder_count"],
            category=category,
            procurement=ProcurementProfile(
                attr=row["procurement_attr"],
                type=row["procurement_type"],
                way=row["award_way"],
            ),
        )

    @staticmethod
    def _row_to_announcement(row: aiosqlite.Row) -> Announcement:
        return Announcement(
            ann_type=AnnouncementType(row["ann_type"]),
            ann_date=_str_to_date(row["ann_date"]),
            tender_seq=row["tender_seq"],
            payload=json.loads(row["payload"]) if row["payload"] else {},
            notice_date=_str_to_date(row["notice_date"]),
            source_url=row["source_url"],
        )


# --------------------------------------------------------------------------
# Vendor Repository
# --------------------------------------------------------------------------
class SqliteVendorRepository:
    """contracts.VendorRepository 的 aiosqlite 實作。"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = aiosqlite.Row

    async def awards_of(self, tax_id: str) -> Sequence[VendorAward]:
        cur = await self._conn.execute(
            """
            SELECT * FROM vendor_awards
            WHERE vendor_tax_id = ?
            ORDER BY awarded_at DESC
            """,
            (tax_id,),
        )
        rows = await cur.fetchall()
        out: list[VendorAward] = []
        for row in rows:
            org_id, job_number = row["tender_id"].split(":", 1)
            out.append(
                VendorAward(
                    vendor_tax_id=row["vendor_tax_id"],
                    vendor_name=row["vendor_name"],
                    tender_id=TenderId(org_id=org_id, job_number=job_number),
                    award_price=Money(
                        amount=row["award_price"],
                        currency=row["award_currency"] or "TWD",
                    ),
                    awarded_at=_str_to_date(row["awarded_at"]),
                )
            )
        return out
