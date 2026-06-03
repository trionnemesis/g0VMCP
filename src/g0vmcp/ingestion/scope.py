"""範圍判定純函式:衛福部機關 / 資訊服務 CPC / 標題關鍵字預篩。

「衛福部 ∩ 資訊服務」ingestion 的篩選真理來源,零 I/O。
"""
from __future__ import annotations

# 資訊服務類 CPC 前綴:45 計算機 / 84 電腦服務 / 47 通訊器材
_IT_CPC_PREFIXES = ("45", "84", "47")

# 黑名單優先:命中即排除(生醫設備 / 試劑 / 清潔等 false positive)
_BLACKLIST = (
    "PCR", "核酸", "蛋白質", "定序", "基因", "微生物",
    "試劑", "耗材", "疫苗", "藥品", "清潔", "培養",
)

_WHITELIST = (
    "資訊", "資安", "系統", "軟體", "硬體", "網站", "入口網", "網路",
    "平臺", "平台", "雲端", "機房", "伺服器", "主機", "數位", "電子化",
    "APP", "API", "資料庫", "委外", "維運", "資通", "電信",
)


def is_mohw(org_name: str) -> bool:
    """機關名稱去空白後是否以「衛生福利部」開頭(涵蓋所有轄下機關)。"""
    return org_name.strip().startswith("衛生福利部")


def is_it_cpc(code: str | None) -> bool:
    """標的分類 CPC 碼是否屬資訊服務類(依前綴判定)。None/空 → False。"""
    if not code:
        return False
    return code.startswith(_IT_CPC_PREFIXES)


def keyword_prescreen(title: str) -> str:
    """標題關鍵字三值預篩。黑名單優先於白名單。

    Returns: "whitelist" / "blacklist" / "unknown"
    """
    if any(kw in title for kw in _BLACKLIST):
        return "blacklist"
    if any(kw in title for kw in _WHITELIST):
        return "whitelist"
    return "unknown"
