#!/usr/bin/env python3
"""一次性純化:把 g0vmcp.db 收斂為「衛福部 ∩ 資訊服務類」。

三類處理:
  1. 非衛福部(agency 不以「衛生福利部」開頭)→ 刪除(目前應為 0,規則保留)。
  2. 已確認非 IT(category_method='official_code' 且 CPC 碼非 45/84/47)→ 刪除
     (enrich 後辨識出的 false positive,如醫療設備被關鍵字誤收)。
  3. 舊假分類(category_code='pcc-tender' 字面占位,非真 CPC 碼)→ 降級為
     llm_fallback baseline,重走 enrich 取真 CPC 碼(不刪,不可信任其 domain_tag)。

刪除依外鍵順序:vendor_awards → announcements → tenders。

使用方式:
  python3 scripts/purge_out_of_scope.py              # dry-run,只列不改
  python3 scripts/purge_out_of_scope.py --apply      # 執行(自動先備份 .bak)
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from g0vmcp.ingestion.scope import is_it_cpc, is_mohw

DB_PATH = Path(__file__).resolve().parents[1] / "g0vmcp.db"


def _classify(conn: sqlite3.Connection):
    """回 (to_delete, to_downgrade)。to_delete=[(tid,agency,title,reason)]。"""
    to_delete, to_downgrade = [], []
    rows = conn.execute(
        "SELECT tender_id, agency, title, category_code, category_method FROM tenders"
    ).fetchall()
    for tid, agency, title, code, method in rows:
        if not is_mohw(agency):
            to_delete.append((tid, agency, title, "非衛福部"))
        elif code == "pcc-tender":
            to_downgrade.append((tid, title))
        elif method == "official_code" and not is_it_cpc(code):
            to_delete.append((tid, agency, title, f"CPC {code!r} 非資訊服務類"))
    return to_delete, to_downgrade


def _apply(conn: sqlite3.Connection, to_delete, to_downgrade) -> None:
    for tid, *_ in to_delete:
        conn.execute("DELETE FROM vendor_awards WHERE tender_id=?", (tid,))
        conn.execute("DELETE FROM announcements WHERE tender_id=?", (tid,))
        conn.execute("DELETE FROM tenders WHERE tender_id=?", (tid,))
    for tid, _title in to_downgrade:
        conn.execute(
            "UPDATE tenders SET category_code='', category_name='', "
            "domain_tag='IT', category_method='llm_fallback' WHERE tender_id=?",
            (tid,),
        )
    conn.commit()


def main(apply: bool) -> None:
    if not DB_PATH.exists():
        print(f"找不到 DB:{DB_PATH}")
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        to_delete, to_downgrade = _classify(conn)
        total = conn.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]

        print(f"DB 現有 {total} 筆。")
        print(f"\n[將刪除 {len(to_delete)} 筆 — 範圍外]")
        for tid, agency, title, reason in to_delete:
            print(f"  - {tid:<22} {reason:<18} {agency[:14]} | {title[:30]}")
        print(f"\n[將降級重抓 {len(to_downgrade)} 筆 — 舊假分類 pcc-tender]")
        for tid, title in to_downgrade:
            print(f"  ~ {tid:<22} {title[:40]}")

        if not apply:
            print("\n(dry-run。加 --apply 執行;執行前會自動備份)")
            return

        backup = DB_PATH.with_suffix(
            f".db.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy(DB_PATH, backup)
        print(f"\n已備份 → {backup.name}")
        _apply(conn, to_delete, to_downgrade)
        remaining = conn.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]
        print(f"純化完成。刪除 {len(to_delete)}、降級 {len(to_downgrade)};剩 {remaining} 筆。")
        if to_downgrade:
            print("提醒:降級者需執行 python3 scripts/enrich_details.py 補真 CPC 碼。")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="純化 DB 為衛福部資訊服務類")
    parser.add_argument("--apply", action="store_true", help="執行(預設 dry-run)")
    args = parser.parse_args()
    main(args.apply)
