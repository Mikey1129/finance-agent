"""taxonomy —— 科目映射规则与单位换算（带版本）。

中文财报优先 CAS 科目，留 IFRS / US GAAP 接口（RAW_TO_STANDARD 可继续扩充）。
单位换算是中文财报准确率最大的坑（元/千元/万元/亿元），此处提供全局单位标尺。
"""

from __future__ import annotations

from domain.financial_fact import StatementType

# 科目映射规则版本（FinancialFact.mapping_rule_version 引用它，满足规则 7）
RULES_VERSION = "taxonomy-cas-v1"

# ---------------------------------------------------------------------------
# 标准科目定义：标准标识符 -> (中文名, 归属报表)
# ---------------------------------------------------------------------------
STANDARD_ITEMS: dict[str, tuple[str, StatementType]] = {
    # 资产负债表
    "cash_and_equivalents": ("货币资金", StatementType.BALANCE_SHEET),
    "accounts_receivable": ("应收账款", StatementType.BALANCE_SHEET),
    "inventory": ("存货", StatementType.BALANCE_SHEET),
    "total_current_assets": ("流动资产合计", StatementType.BALANCE_SHEET),
    "fixed_assets": ("固定资产", StatementType.BALANCE_SHEET),
    "total_assets": ("资产总计", StatementType.BALANCE_SHEET),
    "short_term_borrowings": ("短期借款", StatementType.BALANCE_SHEET),
    "accounts_payable": ("应付账款", StatementType.BALANCE_SHEET),
    "total_current_liabilities": ("流动负债合计", StatementType.BALANCE_SHEET),
    "total_liabilities": ("负债合计", StatementType.BALANCE_SHEET),
    "total_equity": ("所有者权益合计", StatementType.BALANCE_SHEET),
    # 利润表
    "revenue": ("营业收入", StatementType.INCOME_STATEMENT),
    "cost_of_revenue": ("营业成本", StatementType.INCOME_STATEMENT),
    "gross_profit": ("毛利", StatementType.INCOME_STATEMENT),
    "selling_expenses": ("销售费用", StatementType.INCOME_STATEMENT),
    "administrative_expenses": ("管理费用", StatementType.INCOME_STATEMENT),
    "finance_expenses": ("财务费用", StatementType.INCOME_STATEMENT),
    "operating_profit": ("营业利润", StatementType.INCOME_STATEMENT),
    "total_profit": ("利润总额", StatementType.INCOME_STATEMENT),
    "net_profit": ("净利润", StatementType.INCOME_STATEMENT),
    # 现金流量表
    "operating_cash_flow": ("经营活动现金流净额", StatementType.CASH_FLOW),
    "investing_cash_flow": ("投资活动现金流净额", StatementType.CASH_FLOW),
    "financing_cash_flow": ("筹资活动现金流净额", StatementType.CASH_FLOW),
    "net_change_in_cash": ("现金及现金等价物净增加额", StatementType.CASH_FLOW),
}

# 标准科目中文名 -> 标识符（反向索引，用于显示）
_ITEM_ZH = {zh: key for key, (zh, _st) in STANDARD_ITEMS.items()}


# ---------------------------------------------------------------------------
# 原始科目名（含同义词/英文变体）-> 标准科目标识符
# ---------------------------------------------------------------------------
RAW_TO_STANDARD: dict[str, str] = {
    # 资产负债表
    "货币资金": "cash_and_equivalents",
    "现金及现金等价物": "cash_and_equivalents",
    "应收账款": "accounts_receivable",
    "应收帐款": "accounts_receivable",
    "存货": "inventory",
    "流动资产合计": "total_current_assets",
    "固定资产": "fixed_assets",
    "固定资产净额": "fixed_assets",
    "资产总计": "total_assets",
    "总资产": "total_assets",
    "短期借款": "short_term_borrowings",
    "应付账款": "accounts_payable",
    "应付帐款": "accounts_payable",
    "流动负债合计": "total_current_liabilities",
    "负债合计": "total_liabilities",
    "所有者权益合计": "total_equity",
    "股东权益合计": "total_equity",
    "净资产": "total_equity",
    # 利润表
    "营业收入": "revenue",
    "营业总收入": "revenue",
    "主营业务收入": "revenue",
    "销售收入": "revenue",
    "营业成本": "cost_of_revenue",
    "主营业务成本": "cost_of_revenue",
    "营业总成本": "cost_of_revenue",
    "毛利": "gross_profit",
    "毛利润": "gross_profit",
    "销售费用": "selling_expenses",
    "管理费用": "administrative_expenses",
    "财务费用": "finance_expenses",
    "营业利润": "operating_profit",
    "利润总额": "total_profit",
    "净利润": "net_profit",
    "归属于母公司股东的净利润": "net_profit",
    # 现金流量表
    "经营活动产生的现金流量净额": "operating_cash_flow",
    "经营活动现金流量净额": "operating_cash_flow",
    "投资活动产生的现金流量净额": "investing_cash_flow",
    "投资活动现金流量净额": "investing_cash_flow",
    "筹资活动产生的现金流量净额": "financing_cash_flow",
    "筹资活动现金流量净额": "financing_cash_flow",
    "现金及现金等价物净增加额": "net_change_in_cash",
    # 英文（US GAAP / IFRS 接口）
    "revenue": "revenue",
    "sales": "revenue",
    "net sales": "revenue",
    "cost of revenue": "cost_of_revenue",
    "cost of sales": "cost_of_revenue",
    "gross profit": "gross_profit",
    "operating income": "operating_profit",
    "net income": "net_profit",
    "net profit": "net_profit",
    "total assets": "total_assets",
    "total liabilities": "total_liabilities",
    "total equity": "total_equity",
    "shareholders' equity": "total_equity",
    "inventory": "inventory",
    "accounts receivable": "accounts_receivable",
    "accounts payable": "accounts_payable",
    "cash and cash equivalents": "cash_and_equivalents",
    "operating cash flow": "operating_cash_flow",
    "investing cash flow": "investing_cash_flow",
    "financing cash flow": "financing_cash_flow",
}


# ---------------------------------------------------------------------------
# 单位换算：原始单位 -> 换算到「元」的乘数
# ---------------------------------------------------------------------------
UNIT_SCALE: dict[str, float] = {
    "元": 1.0,
    "千元": 1e3,
    "万元": 1e4,
    "亿元": 1e8,
    # 英文
    "thousand": 1e3,
    "thousands": 1e3,
    "million": 1e6,
    "millions": 1e6,
    "billion": 1e9,
    "billions": 1e9,
}


# ---------------------------------------------------------------------------
# 归一化与映射
# ---------------------------------------------------------------------------
def normalize_name(raw: str) -> str:
    """归一化科目名：去首尾空白、全角空格转半角、统一括号。"""
    s = raw.strip()
    s = s.replace("　", " ").replace("　", " ")  # 全角空格
    s = s.replace("（", "(").replace("）", ")")        # 全角括号
    s = s.replace("：", ":").replace("，", ",")
    return s


def map_item(raw_name: str) -> str:
    """原始科目名 -> 标准科目标识符。

    匹配顺序：精确匹配 -> 归一化后精确匹配 -> 归一化后小写匹配。
    未命中时返回归一化后的原始名（保留信息，后续阶段补映射 / 标记 unknown）。
    """
    norm = normalize_name(raw_name)
    if norm in RAW_TO_STANDARD:
        return RAW_TO_STANDARD[norm]
    low = norm.lower()
    if low in RAW_TO_STANDARD:
        return RAW_TO_STANDARD[low]
    return norm  # 未映射，保留原样（调用方可据此判定 unknown）


def is_mapped(raw_name: str) -> bool:
    """科目是否成功映射到标准科目。"""
    return normalize_name(raw_name).lower() in RAW_TO_STANDARD


def item_zh(standard_item: str) -> str:
    """标准科目标识符 -> 中文名。"""
    return STANDARD_ITEMS.get(standard_item, (standard_item,))[0]


def unit_scale(unit: str | None) -> float:
    """原始单位 -> 换算到「元」的乘数。未知单位返回 1.0（不换算）。"""
    if not unit:
        return 1.0
    u = normalize_name(unit).lower()
    return UNIT_SCALE.get(u, UNIT_SCALE.get(u.rstrip("s"), 1.0))


def convert_to_yuan(raw_value: float, unit: str | None) -> float:
    """原始数值 + 单位 -> 统一为「元」的数值。"""
    return raw_value * unit_scale(unit)
