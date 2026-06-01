"""指數退避 helper。

被 Cloudflare 擋下(BlockedError)時,呼叫端用此計算等待秒數並 sleep。
sleep 可注入 → 測試不真的睡。
"""
from __future__ import annotations

from typing import Awaitable, Callable


class ExponentialBackoff:
    """指數退避:delay = base * factor ** attempt,封頂 max_delay。

    Why: 反爬倫理 — 被擋下不可立即高頻重試,須拉長間隔讓對方喘息。
    """

    def __init__(
        self,
        *,
        base: float = 1.0,
        factor: float = 2.0,
        max_delay: float = 60.0,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._base = base
        self._factor = factor
        self._max_delay = max_delay
        self._sleep = sleep or self._default_sleep
        self._attempt = 0

    @staticmethod
    async def _default_sleep(seconds: float) -> None:
        import asyncio

        await asyncio.sleep(seconds)

    def delay_for(self, attempt: int) -> float:
        return min(self._base * (self._factor ** attempt), self._max_delay)

    async def wait(self) -> float:
        """依目前 attempt 計算 delay、sleep,並遞增 attempt。回傳實際等待秒數。"""
        delay = self.delay_for(self._attempt)
        self._attempt += 1
        await self._sleep(delay)
        return delay

    def reset(self) -> None:
        self._attempt = 0
