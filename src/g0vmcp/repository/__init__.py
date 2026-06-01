"""任務② 儲存層 — aiosqlite 實作 contracts.TenderRepository / VendorRepository。"""
from __future__ import annotations

import asyncio

import aiosqlite

from g0vmcp.contracts import TenderRepository, VendorRepository
from g0vmcp.repository.schema import init_db
from g0vmcp.repository.sqlite_repo import (
    SqliteTenderRepository,
    SqliteVendorRepository,
)

__all__ = [
    "init_db",
    "SqliteTenderRepository",
    "SqliteVendorRepository",
    "build_repositories",
]


def build_repositories(
    db_path: str = "g0vmcp.db",
) -> tuple[TenderRepository, VendorRepository]:
    """組合根(composition root):連線 SQLite、建表(冪等),回傳兩個 repository。

    Why asyncio.run: repo 建構子需要已連線的 aiosqlite conn(其 __init__ 即設
    row_factory)。aiosqlite 每次操作取「當前」event loop,故此處(暫時 loop)建立
    的 conn,可在之後 mcp.run() 的 loop 中安全使用 — 由 tests/integration 跨 loop 驗證。
    """

    async def _connect() -> aiosqlite.Connection:
        conn = await aiosqlite.connect(db_path)
        await init_db(conn)
        return conn

    conn = asyncio.run(_connect())
    return SqliteTenderRepository(conn), SqliteVendorRepository(conn)
