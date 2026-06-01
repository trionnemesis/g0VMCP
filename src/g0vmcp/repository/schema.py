"""SQLite schema — 對照 spec/erm.dbml。

僅建立任務② 所需的表(tenders / announcements / vendors / vendor_awards)。
uuid pk 在 SQLite 以 TEXT 表示;timestamp/date 以 ISO 字串存放。
"""
from __future__ import annotations

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenders (
    tender_id        TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL,
    job_number       TEXT NOT NULL,
    agency           TEXT NOT NULL,
    title            TEXT NOT NULL,
    lifecycle_state  TEXT NOT NULL DEFAULT 'TENDERING',
    budget           INTEGER,
    budget_currency  TEXT,
    open_date        TEXT,
    bid_deadline     TEXT,
    base_price       INTEGER,
    base_price_currency TEXT,
    bidder_count     INTEGER,
    procurement_attr TEXT,
    procurement_type TEXT,
    award_way        TEXT,
    category_code    TEXT,
    category_name    TEXT,
    domain_tag       TEXT,
    category_method  TEXT,
    UNIQUE (org_id, job_number)
);

CREATE INDEX IF NOT EXISTS idx_tenders_category_code ON tenders (category_code);
CREATE INDEX IF NOT EXISTS idx_tenders_domain_tag ON tenders (domain_tag);
CREATE INDEX IF NOT EXISTS idx_tenders_state ON tenders (lifecycle_state);

CREATE TABLE IF NOT EXISTS announcements (
    tender_id   TEXT NOT NULL REFERENCES tenders (tender_id),
    ann_type    TEXT NOT NULL,
    tender_seq  TEXT NOT NULL,
    ann_date    TEXT NOT NULL,
    notice_date TEXT,
    payload     TEXT NOT NULL,
    source_url  TEXT,
    PRIMARY KEY (tender_id, tender_seq, ann_type)
);

CREATE INDEX IF NOT EXISTS idx_ann_tender_date ON announcements (tender_id, ann_date);

CREATE TABLE IF NOT EXISTS vendors (
    tax_id  TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    address TEXT
);

CREATE TABLE IF NOT EXISTS vendor_awards (
    id            TEXT PRIMARY KEY,
    vendor_tax_id TEXT NOT NULL REFERENCES vendors (tax_id),
    vendor_name   TEXT NOT NULL,
    tender_id     TEXT NOT NULL REFERENCES tenders (tender_id),
    award_price   INTEGER NOT NULL,
    award_currency TEXT NOT NULL DEFAULT 'TWD',
    awarded_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vendor_awards_tax_id ON vendor_awards (vendor_tax_id);
CREATE INDEX IF NOT EXISTS idx_vendor_awards_tender ON vendor_awards (tender_id);
"""


async def init_db(conn: aiosqlite.Connection) -> None:
    """建立 schema(冪等)。"""
    await conn.executescript(_SCHEMA)
    await conn.commit()
