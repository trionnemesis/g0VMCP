"""Tender 聚合根的富行為 — 包裝 contracts.Tender DTO 落實不變量。

設計:TenderAggregate 持有一個 contracts.Tender DTO(self.tender),
所有狀態變更透過 append_announcement 進行,並收集領域事件。
Repository 透過 .tender 取回 DTO 持久化。
"""
from __future__ import annotations

from datetime import date

from g0vmcp.contracts import (
    Announcement,
    AnnouncementType,
    Tender,
    TenderId,
    TenderState,
)
from g0vmcp.domain.errors import InvariantViolation
from g0vmcp.domain.events import (
    AnnouncementAppended,
    DomainEvent,
    TenderAmended,
    TenderAwarded,
    TenderFailed,
    TenderRegistered,
)

_TERMINAL_STATES = (TenderState.AWARDED, TenderState.FAILED)


class TenderAggregate:
    """Aggregate Root。對 contracts.Tender DTO 施加生命週期不變量。"""

    def __init__(self, tender: Tender) -> None:
        self._tender = tender
        self._events: list[DomainEvent] = []

    # ----- 工廠 -----
    @classmethod
    def register(cls, tender_id: TenderId, agency: str, title: str) -> "TenderAggregate":
        """首見案號 → 建立 TENDERING 狀態的新聚合,產生 TenderRegistered。"""
        tender = Tender(
            tender_id=tender_id,
            agency=agency,
            title=title,
            state=TenderState.TENDERING,
        )
        agg = cls(tender)
        agg._events.append(TenderRegistered(tender_id=tender_id))
        return agg

    @classmethod
    def from_dto(cls, tender: Tender) -> "TenderAggregate":
        """從既有 DTO(Repository 取回)重建聚合,不重放事件。"""
        return cls(tender)

    # ----- 存取 -----
    @property
    def tender(self) -> Tender:
        return self._tender

    @property
    def tender_id(self) -> TenderId:
        return self._tender.tender_id

    @property
    def state(self) -> TenderState:
        return self._tender.state

    @property
    def announcements(self) -> list[Announcement]:
        """依 ann_date 排序的唯一時間線。"""
        return list(self._tender.announcements)

    def pull_events(self) -> list[DomainEvent]:
        """取出並清空累積的領域事件。"""
        events = self._events
        self._events = []
        return events

    # ----- 命令 -----
    def append_announcement(self, ann: Announcement) -> list[DomainEvent]:
        """附加一筆公告並推進生命週期。回傳本次產生的事件。

        不變量:
          - 拒絕重複 (tender_seq, ann_type)
          - 更正公告日不可早於最早招標公告日
          - 終局狀態(AWARDED/FAILED)後狀態不再改變
          - announcements 永遠依 ann_date 排序
        """
        new_events: list[DomainEvent] = []

        self._reject_duplicate(ann)
        self._check_amendment_not_before_tender(ann)

        self._tender.announcements.append(ann)
        self._tender.announcements.sort(key=lambda a: a.ann_date)

        appended = AnnouncementAppended(
            tender_id=self.tender_id,
            ann_type=ann.ann_type,
            ann_date=ann.ann_date,
            tender_seq=ann.tender_seq,
        )
        new_events.append(appended)

        new_events.extend(self._advance_state(ann))

        self._events.extend(new_events)
        return new_events

    # ----- 不變量檢查 -----
    def _reject_duplicate(self, ann: Announcement) -> None:
        for existing in self._tender.announcements:
            if (
                existing.tender_seq == ann.tender_seq
                and existing.ann_type == ann.ann_type
            ):
                raise InvariantViolation(
                    f"重複公告: (tender_seq={ann.tender_seq}, "
                    f"ann_type={ann.ann_type.value}) 已存在"
                )

    def _check_amendment_not_before_tender(self, ann: Announcement) -> None:
        if ann.ann_type is not AnnouncementType.AMENDMENT:
            return
        earliest_tender = self._earliest_tender_date()
        if earliest_tender is not None and ann.ann_date < earliest_tender:
            raise InvariantViolation(
                f"更正公告日 {ann.ann_date} 早於最早招標公告日 {earliest_tender}"
            )

    def _earliest_tender_date(self) -> date | None:
        tender_dates = [
            a.ann_date
            for a in self._tender.announcements
            if a.ann_type is AnnouncementType.TENDER
        ]
        return min(tender_dates) if tender_dates else None

    # ----- 狀態機 -----
    def _advance_state(self, ann: Announcement) -> list[DomainEvent]:
        # 終局狀態不可逆 — 更正等後續公告不改變狀態
        if self._tender.state in _TERMINAL_STATES:
            return []

        if ann.ann_type is AnnouncementType.AWARD:
            self._tender.state = TenderState.AWARDED
            return [TenderAwarded(tender_id=self.tender_id, ann_date=ann.ann_date)]

        if ann.ann_type is AnnouncementType.FAILURE:
            self._tender.state = TenderState.FAILED
            return [TenderFailed(tender_id=self.tender_id, ann_date=ann.ann_date)]

        if ann.ann_type is AnnouncementType.AMENDMENT:
            self._tender.state = TenderState.AMENDED
            return [TenderAmended(tender_id=self.tender_id, ann_date=ann.ann_date)]

        # 招標公告 → 維持 TENDERING
        return []
