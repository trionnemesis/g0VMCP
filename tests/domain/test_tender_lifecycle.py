"""對應 spec/features/tender-lifecycle.feature 的 6 個場景。"""
from __future__ import annotations

from datetime import date

import pytest

from g0vmcp.contracts import Announcement, AnnouncementType, TenderId, TenderState
from g0vmcp.domain import (
    AnnouncementAppended,
    InvariantViolation,
    TenderAggregate,
    TenderAwarded,
    TenderRegistered,
)

TID = TenderId(org_id="3.80.11", job_number="1130108-5")


def _ann(ann_type: AnnouncementType, ann_date: date, seq: str = "01") -> Announcement:
    return Announcement(ann_type=ann_type, ann_date=ann_date, tender_seq=seq)


def _registered() -> TenderAggregate:
    return TenderAggregate.register(TID, agency="某機關", title="某標案")


# 場景: 首見案號時建立標案
def test_first_sighting_registers_tender_in_tendering():
    agg = _registered()
    agg.append_announcement(_ann(AnnouncementType.TENDER, date(2024, 5, 10)))

    events = agg.pull_events()
    assert any(isinstance(e, TenderRegistered) for e in events)
    assert agg.state is TenderState.TENDERING


# 場景: 決標公告推進至終局狀態
def test_award_advances_to_awarded_terminal():
    agg = _registered()
    agg.append_announcement(_ann(AnnouncementType.TENDER, date(2024, 5, 10)))
    new_events = agg.append_announcement(
        _ann(AnnouncementType.AWARD, date(2024, 6, 10), seq="02")
    )

    assert any(isinstance(e, TenderAwarded) for e in new_events)
    assert agg.state is TenderState.AWARDED


# 場景: 更正公告不改變終局
def test_amendment_after_awarded_keeps_state():
    agg = _registered()
    agg.append_announcement(_ann(AnnouncementType.TENDER, date(2024, 5, 10)))
    agg.append_announcement(_ann(AnnouncementType.AWARD, date(2024, 6, 10), seq="02"))
    assert agg.state is TenderState.AWARDED

    agg.append_announcement(_ann(AnnouncementType.AMENDMENT, date(2024, 6, 20), seq="03"))
    assert agg.state is TenderState.AWARDED


# 場景: 公告依日期排序成唯一時間線(亂序附加)
def test_announcements_sorted_into_unique_timeline():
    agg = _registered()
    # 先附加較晚的決標公告(0610),再附加較早的招標公告(0510)
    agg.append_announcement(_ann(AnnouncementType.AWARD, date(2024, 6, 10), seq="02"))
    agg.append_announcement(_ann(AnnouncementType.TENDER, date(2024, 5, 10), seq="01"))

    timeline = agg.announcements
    assert [a.ann_type for a in timeline] == [
        AnnouncementType.TENDER,
        AnnouncementType.AWARD,
    ]
    assert timeline[0].ann_date < timeline[1].ann_date


# 場景: 拒絕重複公告
def test_reject_duplicate_announcement():
    agg = _registered()
    agg.append_announcement(_ann(AnnouncementType.TENDER, date(2024, 5, 10), seq="01"))

    before = len(agg.announcements)
    with pytest.raises(InvariantViolation):
        agg.append_announcement(_ann(AnnouncementType.TENDER, date(2024, 5, 11), seq="01"))
    assert len(agg.announcements) == before  # 不建立重複記錄


# 場景: 違反不變量 — 更正早於招標
def test_amendment_before_tender_raises_invariant():
    agg = _registered()
    agg.append_announcement(_ann(AnnouncementType.TENDER, date(2024, 5, 10), seq="01"))

    with pytest.raises(InvariantViolation):
        agg.append_announcement(
            _ann(AnnouncementType.AMENDMENT, date(2024, 5, 1), seq="02")
        )


# 補強: 無法決標 → FAILED 終局
def test_failure_advances_to_failed_terminal():
    agg = _registered()
    agg.append_announcement(_ann(AnnouncementType.TENDER, date(2024, 5, 10)))
    agg.append_announcement(_ann(AnnouncementType.FAILURE, date(2024, 6, 10), seq="02"))
    assert agg.state is TenderState.FAILED


# 補強: AnnouncementAppended 每次附加都產生
def test_announcement_appended_event_each_time():
    agg = _registered()
    new_events = agg.append_announcement(_ann(AnnouncementType.TENDER, date(2024, 5, 10)))
    assert any(isinstance(e, AnnouncementAppended) for e in new_events)
