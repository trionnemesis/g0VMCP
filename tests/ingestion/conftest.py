"""共用測試工具:fake HTTP getter + fixture 載入。"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from g0vmcp.ingestion.http import Resp

_FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


class FakeHttp:
    """注入式 fake getter — 依 URL 比對規則回應,絕不連網。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._rules: list[tuple[Callable[[str], bool], Resp]] = []
        self._default: Resp | None = None

    def when(self, predicate: Callable[[str], bool], resp: Resp) -> "FakeHttp":
        self._rules.append((predicate, resp))
        return self

    def default(self, resp: Resp) -> "FakeHttp":
        self._default = resp
        return self

    async def __call__(self, url: str) -> Resp:
        self.calls.append(url)
        for predicate, resp in self._rules:
            if predicate(url):
                return resp
        if self._default is not None:
            return self._default
        raise AssertionError(f"no fake rule matched url: {url}")


@pytest.fixture
def fake_http() -> FakeHttp:
    return FakeHttp()
