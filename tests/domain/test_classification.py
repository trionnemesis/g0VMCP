"""對應 spec/features/tender-classification.feature 的 3 個場景。"""
from __future__ import annotations

from g0vmcp.contracts import Category, ProcurementProfile, Tender, TenderId
from g0vmcp.domain import classify, needs_review


def _tender(title: str, category_code: str | None) -> Tender:
    category = (
        Category(code=category_code, name="", domain_tag="", method="")
        if category_code is not None
        else None
    )
    return Tender(
        tender_id=TenderId(org_id="3.80.11", job_number="X-1"),
        agency="某機關",
        title=title,
        category=category,
        procurement=ProcurementProfile(),
    )


class _StubLlm:
    """注入式 LLM stub — 不真的呼叫,固定回傳。"""

    def __init__(self, tag: str) -> None:
        self._tag = tag
        self.called_with: list[str] = []

    def classify_by_title(self, title: str) -> str:
        self.called_with.append(title)
        return self._tag


# 場景: 以官方分類碼歸入 IT 領域(CPC 45 = 計算機及週邊)
def test_classify_it_by_official_code():
    tender = _tender(title="資訊系統建置", category_code="4523")
    category = classify(tender)

    assert category.domain_tag == "IT"
    assert category.method == "official_code"


# 場景: 標題含 IT 字眼但分類碼非 IT — 不誤判
def test_title_says_it_but_code_is_cleaning_not_misclassified():
    # 標題含「資訊」,但 category_code 屬清潔服務(71xx)
    tender = _tender(title="資訊大樓清潔勞務", category_code="71010")
    category = classify(tender)

    assert category.domain_tag != "IT"
    assert category.domain_tag == "清潔"
    assert category.method == "official_code"


# 場景: 缺分類碼時退回 LLM 邊界分類
def test_missing_code_falls_back_to_llm_and_needs_review():
    tender = _tender(title="不明標案", category_code=None)
    llm = _StubLlm(tag="其他")

    category = classify(tender, llm=llm)

    assert category.method == "llm_fallback"
    assert needs_review(category) is True
    assert llm.called_with == ["不明標案"]  # 確實透過注入的 stub


# 補強: 未對應的碼歸「其他」但仍為 official_code
def test_unknown_code_maps_to_other_via_official_code():
    tender = _tender(title="某物資採購", category_code="99999")
    category = classify(tender)

    assert category.domain_tag == "其他"
    assert category.method == "official_code"


# CPC 真實碼:45/84/47 三類皆歸 IT
def test_cpc_computer_peripherals_code_is_it():
    category = classify(_tender(title="伺服器採購", category_code="4523"))
    assert category.domain_tag == "IT"
    assert category.method == "official_code"


def test_cpc_computer_services_code_is_it():
    category = classify(_tender(title="系統維護服務", category_code="8421"))
    assert category.domain_tag == "IT"
    assert category.method == "official_code"


def test_cpc_telecom_equipment_code_is_it():
    category = classify(_tender(title="通訊設備採購", category_code="4712"))
    assert category.domain_tag == "IT"
    assert category.method == "official_code"


# CPC 5159(其他專業工程,真實 live fixture 案例)→ 非 IT
def test_cpc_engineering_code_is_not_it():
    category = classify(_tender(title="昆陽大樓整建工程", category_code="5159"))
    assert category.domain_tag != "IT"
    assert category.domain_tag == "醫療"  # 51 前綴 → 醫療器材/藥品
    assert category.method == "official_code"
