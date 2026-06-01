"""領域事件(過去式)。對照 spec/event-storming.md §2。

frozen dataclass — 事件一旦發生即不可變。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from g0vmcp.contracts import AnnouncementType, Category, TenderId


@dataclass(frozen=True)
class DomainEvent:
    """所有領域事件的基底。"""


@dataclass(frozen=True)
class TenderRegistered(DomainEvent):
    tender_id: TenderId


@dataclass(frozen=True)
class AnnouncementAppended(DomainEvent):
    tender_id: TenderId
    ann_type: AnnouncementType
    ann_date: date
    tender_seq: str


@dataclass(frozen=True)
class TenderAmended(DomainEvent):
    tender_id: TenderId
    ann_date: date


@dataclass(frozen=True)
class TenderAwarded(DomainEvent):
    tender_id: TenderId
    ann_date: date


@dataclass(frozen=True)
class TenderFailed(DomainEvent):
    tender_id: TenderId
    ann_date: date


@dataclass(frozen=True)
class TenderClassified(DomainEvent):
    tender_id: TenderId
    category: Category
