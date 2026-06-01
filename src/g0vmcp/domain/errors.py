"""領域不變量例外。"""
from __future__ import annotations


class InvariantViolation(Exception):
    """聚合不變量被違反時拋出(例:更正公告早於最早招標公告)。"""
