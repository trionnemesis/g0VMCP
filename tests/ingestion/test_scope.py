"""scope.py 純函式測試:機關判定 / CPC IT 判定 / 關鍵字預篩。"""
from __future__ import annotations

import pytest

from g0vmcp.ingestion.scope import is_it_cpc, is_mohw, keyword_prescreen


class TestIsMohw:
    def test_mohw_subagency_is_true(self) -> None:
        assert is_mohw("衛生福利部疾病管制署") is True

    def test_mohw_self_is_true(self) -> None:
        assert is_mohw("衛生福利部") is True

    def test_other_ministry_is_false(self) -> None:
        assert is_mohw("內政部警政署") is False

    def test_leading_whitespace_tolerated(self) -> None:
        assert is_mohw("  衛生福利部食品藥物管理署 ") is True


class TestIsItCpc:
    @pytest.mark.parametrize("code", ["4523", "8421", "8410", "8430", "4712"])
    def test_it_prefixes_true(self, code: str) -> None:
        assert is_it_cpc(code) is True

    @pytest.mark.parametrize("code", ["5159", "3110", ""])
    def test_non_it_false(self, code: str) -> None:
        assert is_it_cpc(code) is False

    def test_none_is_false(self) -> None:
        assert is_it_cpc(None) is False


class TestKeywordPrescreen:
    def test_whitelist_hit(self) -> None:
        assert keyword_prescreen("資訊系統建置案") == "whitelist"

    def test_blacklist_priority_over_whitelist(self) -> None:
        # 「系統」白名單 + 「PCR」黑名單 → 黑名單優先
        assert keyword_prescreen("PCR系統採購") == "blacklist"

    def test_blacklist_clean_false_positive(self) -> None:
        # 「資訊」白名單但「清潔」黑名單 → 剔除
        assert keyword_prescreen("資訊大樓清潔維護委外") == "blacklist"

    def test_unknown(self) -> None:
        assert keyword_prescreen("辦公桌採購") == "unknown"
