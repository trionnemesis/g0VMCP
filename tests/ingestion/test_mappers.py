"""detail_to_tender — ParsedDetail → Tender 映射,分類走官方碼。"""
from __future__ import annotations

from datetime import date, datetime

from g0vmcp.contracts import (
    AnnouncementType,
    AwardedVendor,
    ParsedDetail,
    ProcurementProfile,
    TenderId,
    TenderState,
)
from g0vmcp.ingestion.mappers import detail_to_tender


def _tender_detail(**overrides) -> ParsedDetail:
    base = dict(
        tender_id=TenderId(org_id="3.79.21", job_number="113TFDA-A-513"),
        title="昆陽大樓整建工程採購案",
        agency="衛生福利部食品藥物管理署",
        ann_type=AnnouncementType.TENDER,
        ann_date=date(2025, 5, 1),
        category_code="4523",
        budget=12_500_000,
        open_date=datetime(2025, 5, 8, 10, 0),
        procurement=ProcurementProfile(attr="財物類", type="公開招標"),
        raw={"標的分類": "財物類 4523 - 資訊處理及週邊設備"},
    )
    base.update(overrides)
    return ParsedDetail(**base)


def test_official_code_classifies_it_with_method():
    detail = _tender_detail(category_code="4523")

    tender = detail_to_tender(detail)

    assert tender.category is not None
    assert tender.category.domain_tag == "IT"
    assert tender.category.method == "official_code"
    assert tender.category.code == "4523"
    assert tender.state is TenderState.TENDERING
    assert tender.budget is not None and tender.budget.amount == 12_500_000


def test_no_category_code_leaves_category_none():
    detail = _tender_detail(category_code=None, raw={})

    tender = detail_to_tender(detail)

    assert tender.category is None


def test_award_detail_maps_to_awarded_with_vendors_in_payload():
    detail = _tender_detail(
        ann_type=AnnouncementType.AWARD,
        award_price=10_800_000,
        vendors=[
            AwardedVendor(tax_id="24536789", name="泰安資安股份有限公司", award_price=6_800_000),
        ],
    )

    tender = detail_to_tender(detail)

    assert tender.state is TenderState.AWARDED
    assert len(tender.announcements) == 1
    payload = tender.announcements[0].payload
    assert "vendors" in payload
    assert payload["vendors"][0]["tax_id"] == "24536789"
    assert payload["vendors"][0]["name"] == "泰安資安股份有限公司"
