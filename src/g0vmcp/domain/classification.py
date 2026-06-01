"""標案分類 — 以官方標的分類碼決定 domain_tag,取代標題 ILIKE。

核心原則(對照 tender-classification.feature):
  - 有 category_code → method='official_code',純以碼查表,**完全不看標題**,
    避免「資訊大樓清潔勞務」被誤判為 IT。
  - 缺 category_code → method='llm_fallback',注入式 LLM stub 給邊界判斷,
    並標記 needs_review(人工複核)。
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from g0vmcp.contracts import Category, Tender

# --------------------------------------------------------------------------
# 官方標的分類碼 → domain_tag 對照
# --------------------------------------------------------------------------
# 採用「碼前綴 → 領域」對照。涵蓋 IT / 工程 / 醫療 / 清潔 / 其他。
# 真實採購網標的分類碼以數字段組成;此處以前綴段落歸類。
_CODE_PREFIX_TO_DOMAIN: dict[str, tuple[str, str]] = {
    # prefix: (domain_tag, 人類可讀名稱)
    "31": ("IT", "資訊服務/設備"),       # 資訊軟硬體、系統開發
    "32": ("IT", "通訊/網路設備"),
    "41": ("工程", "土木建築工程"),
    "42": ("工程", "機電工程"),
    "51": ("醫療", "醫療器材/藥品"),
    "52": ("醫療", "醫療服務"),
    "71": ("清潔", "清潔服務"),
    "72": ("清潔", "環境維護"),
}

_NEEDS_REVIEW_KEY = "needs_review"


@runtime_checkable
class LlmClassifier(Protocol):
    """缺分類碼時的邊界分類器(DI 注入,測試用 stub,不真的呼叫 LLM)。"""

    def classify_by_title(self, title: str) -> str:
        """回傳 domain_tag。"""
        ...


def _lookup_by_code(code: str) -> Optional[tuple[str, str]]:
    """以碼前綴查 domain_tag。最長前綴優先。"""
    for length in (2,):
        prefix = code[:length]
        if prefix in _CODE_PREFIX_TO_DOMAIN:
            return _CODE_PREFIX_TO_DOMAIN[prefix]
    return None


def classify(tender: Tender, llm: Optional[LlmClassifier] = None) -> Category:
    """分類標案 → Category。

    有 category_code:純以碼查表(method='official_code')。
      查不到對應 → domain_tag='其他'。
    無 category_code:退回 LLM stub(method='llm_fallback')並標記需人工複核。
    """
    code = _resolve_code(tender)

    if code:
        matched = _lookup_by_code(code)
        if matched is not None:
            domain_tag, name = matched
        else:
            domain_tag, name = "其他", "未對應分類"
        return Category(
            code=code,
            name=name,
            domain_tag=domain_tag,
            method="official_code",
        )

    # 缺碼 → LLM fallback + 需人工複核
    domain_tag = llm.classify_by_title(tender.title) if llm is not None else "其他"
    return Category(
        code="",
        name=f"{_NEEDS_REVIEW_KEY}:{domain_tag}",
        domain_tag=domain_tag,
        method="llm_fallback",
    )


def _resolve_code(tender: Tender) -> Optional[str]:
    """從 tender.category(若已帶碼)取得 category_code。"""
    if tender.category is not None and tender.category.code:
        return tender.category.code
    return None


def needs_review(category: Category) -> bool:
    """是否需人工複核 — llm_fallback 一律需複核。"""
    return category.method == "llm_fallback"
