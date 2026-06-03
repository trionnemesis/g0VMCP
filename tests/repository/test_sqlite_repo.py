"""Repository 往返測試:save→get→search 與 vendor awards。"""
from __future__ import annotations

from datetime import date, datetime

import aiosqlite
import pytest

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
from g0vmcp.repository import (
    SqliteTenderRepository,
    SqliteVendorRepository,
    init_db,
)


@pytest.fixture
async def conn():
    connection = await aiosqlite.connect(":memory:")
    await init_db(connection)
    try:
        yield connection
    finally:
        await connection.close()


def _make_tender(
    org_id: str = "3.80.11",
    job_number: str = "1130108-5",
    title: str = "資訊系統建置案",
    agency: str = "教育部",
    domain_tag: str = "IT",
    budget: int = 5_000_000,
    state: TenderState = TenderState.TENDERING,
    announcements: list[Announcement] | None = None,
) -> Tender:
    return Tender(
        tender_id=TenderId(org_id=org_id, job_number=job_number),
        agency=agency,
        title=title,
        state=state,
        announcements=announcements or [],
        budget=Money(amount=budget),
        open_date=datetime(2024, 6, 1, 9, 30),
        bid_deadline=datetime(2024, 5, 30, 17, 0),
        base_price=Money(amount=4_500_000),
        bidder_count=3,
        category=Category(
            code="31050", name="資訊服務", domain_tag=domain_tag, method="official_code"
        ),
        procurement=ProcurementProfile(attr="勞務類", type="公開招標", way="最有利標"),
    )


async def test_save_then_get_roundtrip(conn):
    repo = SqliteTenderRepository(conn)
    tender = _make_tender(
        announcements=[
            Announcement(
                ann_type=AnnouncementType.TENDER,
                ann_date=date(2024, 5, 10),
                tender_seq="01",
                payload={"foo": "bar"},
                source_url="http://example/detail",
            )
        ]
    )
    await repo.save(tender)

    loaded = await repo.get(str(tender.tender_id))
    assert loaded is not None
    assert loaded.tender_id == tender.tender_id
    assert loaded.agency == "教育部"
    assert loaded.budget == Money(amount=5_000_000)
    assert loaded.open_date == datetime(2024, 6, 1, 9, 30)
    assert loaded.base_price == Money(amount=4_500_000)
    assert loaded.bidder_count == 3
    assert loaded.category.domain_tag == "IT"
    assert loaded.procurement.way == "最有利標"
    assert len(loaded.announcements) == 1
    assert loaded.announcements[0].payload == {"foo": "bar"}
    assert loaded.announcements[0].source_url == "http://example/detail"


async def test_get_missing_returns_none(conn):
    repo = SqliteTenderRepository(conn)
    assert await repo.get("nope:nope") is None


async def test_save_is_upsert(conn):
    repo = SqliteTenderRepository(conn)
    tender = _make_tender(title="原標題")
    await repo.save(tender)

    tender.title = "更新後標題"
    tender.state = TenderState.AWARDED
    await repo.save(tender)

    loaded = await repo.get(str(tender.tender_id))
    assert loaded.title == "更新後標題"
    assert loaded.state is TenderState.AWARDED


async def test_save_merge_preserves_enriched_fields_on_baseline_rerun(conn):
    """兩階段:enrich 補好的加值欄位不應被後續 baseline(None)洗掉。"""
    repo = SqliteTenderRepository(conn)
    # 第一階段:enrich 已補好 open_date/budget/base_price/bidder_count
    enriched = _make_tender()
    await repo.save(enriched)

    # 第二階段:baseline 重跑,加值欄位全 None → COALESCE 應保留舊值
    baseline = Tender(
        tender_id=TenderId(org_id="3.80.11", job_number="1130108-5"),
        agency="教育部",
        title="資訊系統建置案",
        state=TenderState.TENDERING,
        announcements=[],
        budget=None,
        open_date=None,
        bid_deadline=None,
        base_price=None,
        bidder_count=None,
        category=None,
        procurement=ProcurementProfile(attr="勞務類", type="公開招標", way="最有利標"),
    )
    await repo.save(baseline)

    loaded = await repo.get(str(enriched.tender_id))
    assert loaded.open_date == datetime(2024, 6, 1, 9, 30)
    assert loaded.bid_deadline == datetime(2024, 5, 30, 17, 0)
    assert loaded.budget == Money(amount=5_000_000)
    assert loaded.base_price == Money(amount=4_500_000)
    assert loaded.bidder_count == 3


async def test_save_merge_keeps_official_code_over_llm_fallback_baseline(conn):
    """baseline 給 llm_fallback(或空 code)不應退化已存的 official_code 分類。"""
    repo = SqliteTenderRepository(conn)
    # 已有 official_code 分類
    enriched = _make_tender()  # category code=31050 method=official_code
    await repo.save(enriched)

    # baseline 重跑:category code 空字串 + method llm_fallback
    baseline = _make_tender()
    baseline.category = Category(
        code="", name="", domain_tag="IT", method="llm_fallback"
    )
    await repo.save(baseline)

    loaded = await repo.get(str(enriched.tender_id))
    assert loaded.category.code == "31050"
    assert loaded.category.method == "official_code"


async def test_save_still_overwrites_volatile_fields(conn):
    """agency/title/lifecycle_state 等每次都該更新(非 COALESCE)。"""
    repo = SqliteTenderRepository(conn)
    tender = _make_tender(title="原標題", agency="教育部")
    await repo.save(tender)

    tender.title = "更新後標題"
    tender.agency = "衛生福利部"
    tender.state = TenderState.AWARDED
    await repo.save(tender)

    loaded = await repo.get(str(tender.tender_id))
    assert loaded.title == "更新後標題"
    assert loaded.agency == "衛生福利部"
    assert loaded.state is TenderState.AWARDED


async def test_rekey_moves_tender_and_children(conn):
    """org_id 補上後:三表舊鍵列搬到新鍵,舊鍵查無。"""
    repo = SqliteTenderRepository(conn)
    award_ann = Announcement(
        ann_type=AnnouncementType.AWARD,
        ann_date=date(2024, 6, 10),
        tender_seq="02",
        payload={
            "vendors": [
                {"tax_id": "12345678", "name": "好棒科技", "award_price": 4_800_000}
            ]
        },
    )
    tender = Tender(
        tender_id=TenderId(org_id="", job_number="1130108-5"),
        agency="教育部",
        title="無機關代碼案",
        state=TenderState.AWARDED,
        announcements=[
            Announcement(AnnouncementType.TENDER, date(2024, 5, 10), "01"),
            award_ann,
        ],
        budget=Money(amount=5_000_000),
        category=Category(code="31050", name="資訊服務", domain_tag="IT"),
        procurement=ProcurementProfile(attr="勞務類", type="公開招標", way="最有利標"),
    )
    await repo.save(tender)
    old_key = ":1130108-5"

    await repo.rekey(old_key, "3.80.11:1130108-5")

    assert await repo.get(old_key) is None
    moved = await repo.get("3.80.11:1130108-5")
    assert moved is not None
    assert moved.tender_id.org_id == "3.80.11"
    assert len(moved.announcements) == 2
    vendor_repo = SqliteVendorRepository(conn)
    awards = await vendor_repo.awards_of("12345678")
    assert awards[0].tender_id == TenderId(org_id="3.80.11", job_number="1130108-5")


async def test_search_by_domain_tag(conn):
    repo = SqliteTenderRepository(conn)
    await repo.save(_make_tender(job_number="A-1", domain_tag="IT"))
    await repo.save(_make_tender(job_number="B-1", domain_tag="清潔"))

    results = await repo.search(domain_tag="IT")
    assert len(results) == 1
    assert results[0].category.domain_tag == "IT"


async def test_search_by_state(conn):
    repo = SqliteTenderRepository(conn)
    await repo.save(_make_tender(job_number="A-1", state=TenderState.TENDERING))
    await repo.save(_make_tender(job_number="B-1", state=TenderState.AWARDED))

    tendering = await repo.search(state="TENDERING")
    assert [t.tender_id.job_number for t in tendering] == ["A-1"]
    assert tendering[0].state is TenderState.TENDERING


async def test_search_by_budget_range(conn):
    repo = SqliteTenderRepository(conn)
    await repo.save(_make_tender(job_number="A-1", budget=1_000_000))
    await repo.save(_make_tender(job_number="B-1", budget=9_000_000))

    results = await repo.search(budget_min=2_000_000, budget_max=10_000_000)
    assert len(results) == 1
    assert results[0].budget == Money(amount=9_000_000)


async def test_search_by_keyword_and_agency(conn):
    repo = SqliteTenderRepository(conn)
    await repo.save(_make_tender(job_number="A-1", title="校園網路建置", agency="教育部"))
    await repo.save(_make_tender(job_number="B-1", title="道路鋪設", agency="交通部"))

    by_keyword = await repo.search(keyword="網路")
    assert [t.tender_id.job_number for t in by_keyword] == ["A-1"]

    by_agency = await repo.search(agency="交通部")
    assert [t.tender_id.job_number for t in by_agency] == ["B-1"]


async def test_search_by_date_range(conn):
    repo = SqliteTenderRepository(conn)
    await repo.save(
        _make_tender(
            job_number="A-1",
            announcements=[
                Announcement(AnnouncementType.TENDER, date(2024, 1, 5), "01")
            ],
        )
    )
    await repo.save(
        _make_tender(
            job_number="B-1",
            announcements=[
                Announcement(AnnouncementType.TENDER, date(2024, 12, 20), "01")
            ],
        )
    )

    results = await repo.search(date_from=date(2024, 6, 1), date_to=date(2024, 12, 31))
    assert [t.tender_id.job_number for t in results] == ["B-1"]


async def test_search_respects_limit(conn):
    repo = SqliteTenderRepository(conn)
    for i in range(5):
        await repo.save(_make_tender(job_number=f"X-{i}"))

    results = await repo.search(limit=2)
    assert len(results) == 2


async def test_vendor_awards_projected_from_award_announcement(conn):
    tender_repo = SqliteTenderRepository(conn)
    vendor_repo = SqliteVendorRepository(conn)

    award_ann = Announcement(
        ann_type=AnnouncementType.AWARD,
        ann_date=date(2024, 6, 10),
        tender_seq="02",
        payload={
            "vendors": [
                {"tax_id": "12345678", "name": "好棒科技", "award_price": 4_800_000},
            ]
        },
    )
    tender = _make_tender(
        state=TenderState.AWARDED,
        announcements=[
            Announcement(AnnouncementType.TENDER, date(2024, 5, 10), "01"),
            award_ann,
        ],
    )
    await tender_repo.save(tender)

    awards = await vendor_repo.awards_of("12345678")
    assert len(awards) == 1
    assert awards[0].vendor_name == "好棒科技"
    assert awards[0].award_price == Money(amount=4_800_000)
    assert awards[0].awarded_at == date(2024, 6, 10)
    assert awards[0].tender_id == tender.tender_id


async def test_vendor_awards_empty_for_unknown(conn):
    vendor_repo = SqliteVendorRepository(conn)
    assert await vendor_repo.awards_of("00000000") == []
