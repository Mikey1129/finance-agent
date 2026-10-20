"""taxonomy —— 科目映射规则与单位换算（带版本）。

中文财报优先 CAS 科目，留 IFRS / US GAAP 接口（RAW_TO_STANDARD 可继续扩充）。
单位换算是中文财报准确率最大的坑（元/千元/万元/亿元），此处提供全局单位标尺。
"""

from __future__ import annotations

import re

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
    "trading_financial_assets": ("交易性金融资产", StatementType.BALANCE_SHEET),
    "notes_receivable": ("应收票据", StatementType.BALANCE_SHEET),
    "prepayments": ("预付款项", StatementType.BALANCE_SHEET),
    "other_receivables": ("其他应收款", StatementType.BALANCE_SHEET),
    "long_term_equity_investment": ("长期股权投资", StatementType.BALANCE_SHEET),
    "construction_in_progress": ("在建工程", StatementType.BALANCE_SHEET),
    "intangible_assets": ("无形资产", StatementType.BALANCE_SHEET),
    "goodwill": ("商誉", StatementType.BALANCE_SHEET),
    "deferred_tax_assets": ("递延所得税资产", StatementType.BALANCE_SHEET),
    "contract_liabilities": ("合同负债", StatementType.BALANCE_SHEET),
    "notes_payable": ("应付票据", StatementType.BALANCE_SHEET),
    "advances_from_customers": ("预收款项", StatementType.BALANCE_SHEET),
    "employee_benefits_payable": ("应付职工薪酬", StatementType.BALANCE_SHEET),
    "taxes_payable": ("应交税费", StatementType.BALANCE_SHEET),
    "other_payables": ("其他应付款", StatementType.BALANCE_SHEET),
    "long_term_borrowings": ("长期借款", StatementType.BALANCE_SHEET),
    "paid_in_capital": ("实收资本", StatementType.BALANCE_SHEET),
    "capital_reserve": ("资本公积", StatementType.BALANCE_SHEET),
    "surplus_reserve": ("盈余公积", StatementType.BALANCE_SHEET),
    "undistributed_profit": ("未分配利润", StatementType.BALANCE_SHEET),
    "total_non_current_assets": ("非流动资产合计", StatementType.BALANCE_SHEET),
    "total_non_current_liabilities": ("非流动负债合计", StatementType.BALANCE_SHEET),
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
    "taxes_and_surcharges": ("税金及附加", StatementType.INCOME_STATEMENT),
    "rnd_expenses": ("研发费用", StatementType.INCOME_STATEMENT),
    "investment_income": ("投资收益", StatementType.INCOME_STATEMENT),
    "non_operating_income": ("营业外收入", StatementType.INCOME_STATEMENT),
    "non_operating_expenses": ("营业外支出", StatementType.INCOME_STATEMENT),
    "income_tax_expense": ("所得税费用", StatementType.INCOME_STATEMENT),
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
    "交易性金融资产": "trading_financial_assets",
    "应收票据": "notes_receivable",
    "应收款项融资": "notes_receivable",
    "预付款项": "prepayments",
    "预付账款": "prepayments",
    "其他应收款": "other_receivables",
    "长期股权投资": "long_term_equity_investment",
    "在建工程": "construction_in_progress",
    "无形资产": "intangible_assets",
    "商誉": "goodwill",
    "递延所得税资产": "deferred_tax_assets",
    "合同负债": "contract_liabilities",
    "应付票据": "notes_payable",
    "预收款项": "advances_from_customers",
    "预收账款": "advances_from_customers",
    "应付职工薪酬": "employee_benefits_payable",
    "应交税费": "taxes_payable",
    "其他应付款": "other_payables",
    "长期借款": "long_term_borrowings",
    "实收资本": "paid_in_capital",
    "股本": "paid_in_capital",
    "资本公积": "capital_reserve",
    "盈余公积": "surplus_reserve",
    "未分配利润": "undistributed_profit",
    "非流动资产合计": "total_non_current_assets",
    "非流动负债合计": "total_non_current_liabilities",
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
    "税金及附加": "taxes_and_surcharges",
    "研发费用": "rnd_expenses",
    "投资收益": "investment_income",
    "营业外收入": "non_operating_income",
    "营业外支出": "non_operating_expenses",
    "所得税费用": "income_tax_expense",
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
def _compact(s: str) -> str:
    """去所有空白（用于紧凑匹配）。"""
    return re.sub(r"\s+", "", s)


# 紧凑索引：键为去空白后的科目名，处理「所有者权益（或股东\n权益）合计」这类带换行/括号的科目
_COMPACT_INDEX = {_compact(k): v for k, v in RAW_TO_STANDARD.items()}


def normalize_name(raw: str) -> str:
    """归一化科目名：去换行/制表、全角转半角、去括号同义补充、压缩空白。"""
    s = raw.strip()
    s = re.sub(r"[\n\r\t]+", " ", s)          # PDF 单元格常带换行
    s = s.replace("　", " ").replace("（", "(").replace("）", ")")
    s = s.replace("：", ":").replace("，", ",")
    s = re.sub(r"\([^)]*\)", "", s)           # 去括号同义补充：「所有者权益（或股东权益）合计」
    s = re.sub(r"\s+", " ", s).strip()
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
    compact = _compact(low)
    if compact in _COMPACT_INDEX:
        return _COMPACT_INDEX[compact]
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
