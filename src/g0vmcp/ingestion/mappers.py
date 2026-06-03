"""ParsedDetail → Tender 映射。

從 scripts/ingest_sample.py 萃取,分類改走 domain.classification.classify():
有 category_code 時以官方碼分類(method='official_code',完全不看標題);無碼則
維持 None(由呼叫端決定是否走 llm_fallback)。state 由公告類型對應。
"""
from __future__ import annotations

from g0vmcp.contracts import (
    Announcement,
    AnnouncementType,
    Category,
    Money,
    ParsedDetail,
    Tender,
    TenderState,
)
from g0vmcp.domain import classify

_ANN_TYPE_TO_STATE: dict[AnnouncementType, TenderState] = {
    AnnouncementType.AWARD: TenderState.AWARDED,
    AnnouncementType.FAILURE: TenderState.FAILED,
    AnnouncementType.AMENDMENT: TenderState.AMENDED,
}


def _state_from_ann_type(ann_type: AnnouncementType) -> TenderState:
    return _ANN_TYPE_TO_STATE.get(ann_type, TenderState.TENDERING)


def detail_to_tender(detail: ParsedDetail) -> Tender:
    """ParsedDetail → Tender(DTO)。決標公告把 vendors 併入 announcement payload。"""
    vendor_list = [
        {"tax_id": v.tax_id, "name": v.name, "award_price": v.award_price}
        for v in detail.vendors
    ]
    payload: dict = {**detail.raw}
    if vendor_list:
        payload["vendors"] = vendor_list

    ann = Announcement(
        ann_type=detail.ann_type,
        ann_date=detail.ann_date,
        tender_seq=detail.tender_seq,
        notice_date=detail.notice_date,
        source_url=detail.source_url,
        payload=payload,
    )

    tender = Tender(
        tender_id=detail.tender_id,
        agency=detail.agency,
        title=detail.title,
        state=_state_from_ann_type(detail.ann_type),
        announcements=[ann],
        budget=Money(detail.budget) if detail.budget else None,
        open_date=detail.open_date,
        bid_deadline=detail.bid_deadline,
        base_price=Money(detail.base_price) if detail.base_price else None,
        bidder_count=detail.bidder_count,
        category=None,
        procurement=detail.procurement,
    )

    # 有官方碼 → 以碼分類(不看標題);無碼維持 None,交由呼叫端決定 llm_fallback。
    # classify() 從 tender.category.code 取碼,故先掛上僅含碼的占位 Category。
    if detail.category_code:
        tender.category = Category(
            code=detail.category_code,
            name=detail.raw.get("標的分類", ""),
            domain_tag="",
            method="",
        )
        classified = classify(tender)
        # 保留明細頁原始「標的分類」描述當 name,domain_tag/method 採官方碼結果
        tender.category = Category(
            code=classified.code,
            name=detail.raw.get("標的分類", "") or classified.name,
            domain_tag=classified.domain_tag,
            method=classified.method,
        )

    return tender
