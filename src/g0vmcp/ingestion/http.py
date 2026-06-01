"""DI 邊界:抽象 async HTTP getter。

正式邏輯絕不直接相依 httpx/web.pcc.gov.tw — fetcher 透過此 Protocol 取得回應,
測試時注入 fake getter(反爬倫理 + 可測性)。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Resp:
    """最小 HTTP 回應契約。"""

    status_code: int
    text: str
    url: str = ""


@runtime_checkable
class HttpGetter(Protocol):
    async def __call__(self, url: str) -> Resp: ...
