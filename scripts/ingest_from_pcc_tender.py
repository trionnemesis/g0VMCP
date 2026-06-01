#!/usr/bin/env python3
"""pcc-tender 開放資料 → g0vmcp.db（不經過 web 爬蟲）。

擷取範圍：衛生福利部體系（部本部 + 疾管署 / 食藥署 / 健保署 / 國健署 +
所屬部立醫院、療養院、教養院、老人之家等，agency ILIKE '%衛生福利部%'）。
資料列由 twinkle-hub `query_rows("pcc-tender", ...)` 查得，欄位直接對應 Tender DTO。

兩類資料：
- PCC_ROWS：決標公告（state=AWARDED），近年依金額排序的代表性標案。
- TENDER_ROWS：招標公告且「無對應決標公告」（state=TENDERING，進行中/尚未決標），
  讓 search_tenders(state="TENDERING") 有實際資料可查。

開標時間/截止投標/底價/預算等加值欄位 pcc-tender 不含，由 enrich_open_date.py
從 web.pcc.gov.tw 明細頁回填（本腳本只建立 baseline）。

使用方式: python3.11 scripts/ingest_from_pcc_tender.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from g0vmcp.contracts import (
    Announcement,
    AnnouncementType,
    Category,
    Money,
    ProcurementProfile,
    Tender,
    TenderId,
    TenderState,
)
from g0vmcp.repository import build_repositories

DB_PATH = str(Path(__file__).resolve().parents[1] / "g0vmcp.db")

# 衛生福利部體系決標公告（由 twinkle-hub query_rows("pcc-tender") 查得，
# where=agency ILIKE '%衛生福利部%' AND announcement_type='決標公告'，近年依金額排序）
PCC_ROWS = [
    {
        "job_number": "CK113019",
        "agency": "衛生福利部疾病管制署",
        "title": "113年新型冠狀病毒(SARS-CoV-2)感染症患者治療用口服抗病毒藥物Paxlovid 50萬人份療程採購案(含倉儲配送)",
        "award_price": 10735515000,
        "companies": "久裕企業股份有限公司",
        "procurement_attr": "財物類",
        "procurement_type": "限制性招標(未經公開評選或公開徵求)",
        "award_way": "最低標",
        "date": "2024-07-17",
        "notice_date": "2024-07-24",
    },
    {
        "job_number": "HP114036",
        "agency": "衛生福利部疾病管制署",
        "title": "115-117年度Moderna COVID-19疫苗",
        "award_price": 9922500000,
        "companies": "莫德納台灣股份有限公司 (Moderna Taiwan Co. Ltd.)",
        "procurement_attr": "財物類",
        "procurement_type": "限制性招標(未經公開評選或公開徵求)",
        "award_way": "最低標",
        "date": "2025-11-24",
        "notice_date": "2025-12-01",
    },
    {
        "job_number": "CK113021",
        "agency": "衛生福利部疾病管制署",
        "title": "113年新型冠狀病毒(SARS-CoV-2)感染症患者治療用抗病毒藥物Remdesivir 300,000劑(含倉儲配送)採購案",
        "award_price": 3541500000,
        "companies": "華安藥品股份有限公司",
        "procurement_attr": "財物類",
        "procurement_type": "限制性招標(未經公開評選或公開徵求)",
        "award_way": "最低標",
        "date": "2024-09-02",
        "notice_date": "2024-09-03",
    },
    {
        "job_number": "A1140201",
        "agency": "衛生福利部國民健康署",
        "title": "114-117年「人類乳突病毒疫苗採購計畫」",
        "award_price": 1824000000,
        "companies": "美商默沙東藥廠股份有限公司台灣分公司",
        "procurement_attr": "財物類",
        "procurement_type": "限制性招標(未經公開評選或公開徵求)",
        "award_way": "最低標",
        "date": "2025-03-07",
        "notice_date": "2025-03-10",
    },
    {
        "job_number": "CL113005",
        "agency": "衛生福利部疾病管制署",
        "title": "113年度四價流感疫苗",
        "award_price": 1622451600,
        "companies": "國光生物科技股份有限公司",
        "procurement_attr": "財物類",
        "procurement_type": "限制性招標(未經公開評選或公開徵求)",
        "award_way": "最有利標",
        "date": "2024-04-15",
        "notice_date": "2024-04-16",
    },
    {
        "job_number": "CL114008",
        "agency": "衛生福利部疾病管制署",
        "title": "114年度三價流感疫苗採購案",
        "award_price": 1573612770,
        "companies": "國光生物科技股份有限公司",
        "procurement_attr": "財物類",
        "procurement_type": "限制性招標(未經公開評選或公開徵求)",
        "award_way": "最有利標",
        "date": "2025-04-23",
        "notice_date": "2025-05-02",
    },
    {
        "job_number": "113TFDA-A-513",
        "agency": "衛生福利部食品藥物管理署",
        "title": "昆陽大樓整建工程採購案",
        "award_price": 1437749369,
        "companies": "群光電能科技股份有限公司 (Chicony Power Technology Co., Ltd.)",
        "procurement_attr": "工程類",
        "procurement_type": "公開招標",
        "award_way": "最有利標",
        "date": "2025-06-05",
        "notice_date": "2025-06-26",
    },
    {
        "job_number": "K1130690496",
        "agency": "衛生福利部中央健康保險署",
        "title": "第三代醫療資訊系統建置案",
        "award_price": 963800000,
        "companies": "資拓宏宇國際股份有限公司 (International Integrated Systems, Inc.)",
        "procurement_attr": "勞務類",
        "procurement_type": "經公開評選或公開徵求之限制性招標",
        "award_way": "準用最有利標",
        "date": "2024-12-27",
        "notice_date": "2025-01-09",
    },
    {
        "job_number": "CK113022",
        "agency": "衛生福利部疾病管制署",
        "title": "113年白喉、破傷風、非細胞性百日咳、不活化小兒麻痺及b型嗜血桿菌五合一疫苗(DTaP-IPV-Hib) 160萬劑採購案",
        "award_price": 958400000,
        "companies": "賽諾菲股份有限公司",
        "procurement_attr": "財物類",
        "procurement_type": "限制性招標(未經公開評選或公開徵求)",
        "award_way": "最低標",
        "date": "2024-09-02",
        "notice_date": "2024-09-11",
    },
    {
        "job_number": "HP114019",
        "agency": "衛生福利部疾病管制署",
        "title": "115-117年細胞培養日本腦炎疫苗78萬劑",
        "award_price": 608400000,
        "companies": "裕利股份有限公司",
        "procurement_attr": "財物類",
        "procurement_type": "限制性招標(未經公開評選或公開徵求)",
        "award_way": "最低標",
        "date": "2025-09-23",
        "notice_date": "2025-09-25",
    },
    {
        "job_number": "LA112033",
        "agency": "衛生福利部疾病管制署",
        "title": "113-116年度麻疹腮腺炎德國麻疹(MMR)混合疫苗95萬劑",
        "award_price": 310075000,
        "companies": "美商默沙東藥廠股份有限公司台灣分公司",
        "procurement_attr": "財物類",
        "procurement_type": "限制性招標(未經公開評選或公開徵求)",
        "award_way": "最低標",
        "date": "2024-01-29",
        "notice_date": "2024-01-31",
    },
    {
        "job_number": "M1413002",
        "agency": "衛生福利部",
        "title": "114-115年度所屬醫院共用醫院資訊系統暨文件表單系統維護諮詢及增修委外服務案",
        "award_price": 105900000,
        "companies": "大同醫護股份有限公司 (Tatung Medical & Healthcare Technologies Co., Ltd.)",
        "procurement_attr": "勞務類",
        "procurement_type": "經公開評選或公開徵求之限制性招標",
        "award_way": "準用最有利標",
        "date": "2024-12-17",
        "notice_date": "2024-12-20",
    },
    {
        "job_number": "113TFDA-S-501",
        "agency": "衛生福利部食品藥物管理署",
        "title": "現代化食品藥物國家級實驗大樓實驗室桌櫃暨廢氣排放系統採購案",
        "award_price": 140000000,
        "companies": "禮學社股份有限公司",
        "procurement_attr": "財物類",
        "procurement_type": "公開招標",
        "award_way": "最有利標",
        "date": "2025-03-27",
        "notice_date": "2025-04-16",
    },
    {
        "job_number": "W1130690262",
        "agency": "衛生福利部中央健康保險署",
        "title": "113年度健保資訊設備建置服務採購案",
        "award_price": 160800000,
        "companies": "資拓宏宇國際股份有限公司",
        "procurement_attr": "勞務類",
        "procurement_type": "經公開評選或公開徵求之限制性招標",
        "award_way": "準用最有利標",
        "date": "2024-07-18",
        "notice_date": "2024-08-06",
    },
]

# 尚未決標的招標公告(由 query_rows("pcc-tender", announcement_type='招標公告') 查得,
# 交叉比對確認「無對應決標公告」=進行中)。state=TENDERING,無得標廠商/決標金額;
# 開標時間/截止投標/預算等加值欄位由 enrich_open_date.py 從明細頁回填。
TENDER_ROWS = [
    {
        "job_number": "CY115008",
        "agency": "衛生福利部疾病管制署",
        "title": "「115年度培養基及抗血清等試劑耗材乙批」採購案",
        "procurement_attr": "財物類",
        "procurement_type": "公開招標",
        "date": "2026-04-29",
    },
    {
        "job_number": "KA115011",
        "agency": "衛生福利部疾病管制署",
        "title": "「115年核酸定序」採購案",
        "procurement_attr": "勞務類",
        "procurement_type": "公開招標",
        "date": "2026-04-07",
    },
    {
        "job_number": "115-2-009",
        "agency": "衛生福利部國家中醫藥研究所",
        "title": "奈米流式檢測儀1組",
        "procurement_attr": "財物類",
        "procurement_type": "公開招標",
        "date": "2026-04-23",
    },
    {
        "job_number": "K1140665893",
        "agency": "衛生福利部中央健康保險署",
        "title": "115年在宅醫療照護資訊平台規劃及專業技術服務案",
        "procurement_attr": "勞務類",
        "procurement_type": "經公開評選或公開徵求之限制性招標",
        "date": "2026-03-25",
    },
    {
        "job_number": "115TFDA-A-205",
        "agency": "衛生福利部食品藥物管理署",
        "title": "115年度「市售產品之乳酸菌抗藥特性分析」",
        "procurement_attr": "勞務類",
        "procurement_type": "經公開評選或公開徵求之限制性招標",
        "date": "2026-03-24",
    },
    {
        "job_number": "M1509239",
        "agency": "衛生福利部",
        "title": "115年度中英文網站維運及功能擴充案",
        "procurement_attr": "勞務類",
        "procurement_type": "經公開評選或公開徵求之限制性招標",
        "date": "2026-03-03",
    },
    {
        "job_number": "M1522154",
        "agency": "衛生福利部",
        "title": "115年文宣暨活動通路集中採購案",
        "procurement_attr": "勞務類",
        "procurement_type": "公開招標",
        "date": "2026-02-03",
    },
]


def _infer_domain(attr: str | None) -> str:
    if attr == "工程類":
        return "工程"
    if attr == "財物類":
        return "財物"
    if attr == "勞務類":
        return "勞務"
    return "其他"


def _row_to_tender(row: dict) -> Tender:
    ann_date = date.fromisoformat(row["date"])
    notice_date = date.fromisoformat(row["notice_date"]) if row.get("notice_date") else None

    vendor_list = []
    if row.get("companies"):
        # pcc-tender companies 是文字，沒有 tax_id；用空字串占位
        vendor_list = [{"tax_id": "", "name": row["companies"], "award_price": row["award_price"]}]

    payload: dict = {}
    if vendor_list:
        payload["vendors"] = vendor_list

    ann = Announcement(
        ann_type=AnnouncementType.AWARD,
        ann_date=ann_date,
        tender_seq="01",
        notice_date=notice_date,
        source_url=None,
        payload=payload,
    )

    domain = _infer_domain(row.get("procurement_attr"))
    category = Category(
        code="pcc-tender",
        name=row.get("procurement_attr", ""),
        domain_tag=domain,
    )

    return Tender(
        tender_id=TenderId(org_id="", job_number=row["job_number"]),
        agency=row["agency"],
        title=row["title"],
        state=TenderState.AWARDED,
        announcements=[ann],
        budget=Money(row["award_price"]) if row.get("award_price") else None,
        open_date=None,
        bid_deadline=None,
        base_price=None,
        bidder_count=None,
        category=category,
        procurement=ProcurementProfile(
            attr=row.get("procurement_attr"),
            type=row.get("procurement_type"),
            way=row.get("award_way"),
        ),
    )


def _tender_row_to_tender(row: dict) -> Tender:
    """招標公告(尚未決標)→ TENDERING tender;無得標廠商/決標金額。"""
    domain = _infer_domain(row.get("procurement_attr"))
    return Tender(
        tender_id=TenderId(org_id="", job_number=row["job_number"]),
        agency=row["agency"],
        title=row["title"],
        state=TenderState.TENDERING,
        announcements=[
            Announcement(
                ann_type=AnnouncementType.TENDER,
                ann_date=date.fromisoformat(row["date"]),
                tender_seq="01",
                source_url=None,
                payload={},
            )
        ],
        budget=None,
        open_date=None,
        bid_deadline=None,
        base_price=None,
        bidder_count=None,
        category=Category(
            code="pcc-tender",
            name=row.get("procurement_attr", ""),
            domain_tag=domain,
        ),
        procurement=ProcurementProfile(
            attr=row.get("procurement_attr"),
            type=row.get("procurement_type"),
            way=None,
        ),
    )


async def main(tender_repo) -> None:
    # clear existing data
    await tender_repo._conn.execute("DELETE FROM vendor_awards")
    await tender_repo._conn.execute("DELETE FROM vendors")
    await tender_repo._conn.execute("DELETE FROM announcements")
    await tender_repo._conn.execute("DELETE FROM tenders")
    await tender_repo._conn.commit()
    print(
        f"清除舊資料，準備寫入 {len(PCC_ROWS)} 筆決標 + "
        f"{len(TENDER_ROWS)} 筆尚未決標"
    )

    for row in PCC_ROWS:
        tender = _row_to_tender(row)
        await tender_repo.save(tender)
        print(f"  saved {tender.tender_id!s:<30} {tender.state.value:<10} {tender.agency[:24]}")
    for row in TENDER_ROWS:
        tender = _tender_row_to_tender(row)
        await tender_repo.save(tender)
        print(f"  saved {tender.tender_id!s:<30} {tender.state.value:<10} {tender.agency[:24]}")

    print("\n=== DB 現況 ===")
    results = await tender_repo.search(limit=20)
    print(f"共 {len(results)} 筆")
    for t in results:
        bgt = f"{t.budget.amount:,}" if t.budget else "-"
        print(
            f"  {t.tender_id!s:<35} {t.state.value:<12} "
            f"預算={bgt:<18} {t.agency[:25]}"
        )


if __name__ == "__main__":
    tender_repo, _ = build_repositories(DB_PATH)
    asyncio.run(main(tender_repo))
