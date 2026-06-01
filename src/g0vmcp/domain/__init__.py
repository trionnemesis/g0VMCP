"""任務② 領域層 — 富行為聚合、領域事件、分類。

contracts.Tender 為貧血 DTO;本套件提供 TenderAggregate 包裝 DTO 並落實不變量,
Repository 對外仍以 contracts.Tender DTO 進出。
"""
from g0vmcp.domain.errors import InvariantViolation
from g0vmcp.domain.events import (
    AnnouncementAppended,
    DomainEvent,
    TenderAmended,
    TenderAwarded,
    TenderClassified,
    TenderFailed,
    TenderRegistered,
)
from g0vmcp.domain.tender import TenderAggregate
from g0vmcp.domain.classification import LlmClassifier, classify, needs_review

__all__ = [
    "InvariantViolation",
    "DomainEvent",
    "TenderRegistered",
    "AnnouncementAppended",
    "TenderAmended",
    "TenderAwarded",
    "TenderFailed",
    "TenderClassified",
    "TenderAggregate",
    "classify",
    "needs_review",
    "LlmClassifier",
]
