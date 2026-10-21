"""metrics —— 财务指标注册中心与确定性计算。

方案第 8 章指标体系（第一版 6 组 16 指标）+ 第 6 章 MetricDefinition。
财务指标必须由确定性计算函数产生（方案 5.3 路径 B），分母为 0 或缺失时不得返回「看似正常的数」。
口径差异大的指标（ROE/增长率/现金流比/周转率）在 definition.description 说明口径，MetricResult.formula 随结果展示。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from domain import FinancialFact, StatementType


class MetricStatus(str, Enum):
    """指标计算状态。"""

    COMPUTED = "computed"                  # 正常算出
    INSUFFICIENT_DATA = "insufficient_data"  # 缺科目/缺上期，无法计算
    DIVIDE_BY_ZERO = "divide_by_zero"      # 分母为 0，不返回假数


@dataclass
class MetricDefinition:
    """指标定义（方案第 6 章 MetricDefinition）：公式 / 口径 / 所需科目 / 适用报表。"""

    code: str
    name: str
    group: str
    formula: str                        # 人类可读公式（展示与追溯）
    statement_type: StatementType       # 主要来源报表
    kind: str                           # ratio | growth | turnover | change | raw | divergence
    numerator: tuple[str, ...] = ()     # ratio：分子科目（加和）
    subtract: tuple[str, ...] = ()      # ratio：分子中减去的科目（速动比率减存货、毛利率减营业成本）
    denominator: tuple[str, ...] = ()   # ratio：分母科目（加和）
    item: str = ""                      # growth / change / raw：目标科目
    flow_item: str = ""                 # turnover：流量科目（营收/营业成本）
    point_item: str = ""                # turnover：时点科目（应收/存货，期初期末取平均）
    description: str = ""               # 口径说明
    is_signal: bool = False             # 财务质量组：信号而非结论


@dataclass
class MetricResult:
    """一次指标计算结果，保留分子/分母/原始科目输入以便追溯。"""

    code: str
    name: str
    group: str
    period: str
    scope: str
    formula: str
    value: float | None = None
    status: MetricStatus = MetricStatus.INSUFFICIENT_DATA
    numerator: float | None = None
    denominator: float | None = None
    inputs: dict[str, float | None] = field(default_factory=dict)
    is_signal: bool = False

    @property
    def computed(self) -> bool:
        return self.status is MetricStatus.COMPUTED


# ---------------------------------------------------------------------------
# 指标注册表（第一版 6 组 16 指标）
# ---------------------------------------------------------------------------
METRIC_DEFINITIONS: dict[str, MetricDefinition] = {
    # -- 成长能力 ----------------------------------------------------------
    "revenue_growth": MetricDefinition(
        "revenue_growth", "营收增长率", "成长能力",
        "营收增长率 = (本期营业收入 - 上期营业收入) / |上期营业收入|",
        StatementType.INCOME_STATEMENT, "growth", item="revenue",
        description="同比口径，分母用上期绝对值。",
    ),
    "net_profit_growth": MetricDefinition(
        "net_profit_growth", "净利润增长率", "成长能力",
        "净利润增长率 = (本期净利润 - 上期净利润) / |上期净利润|",
        StatementType.INCOME_STATEMENT, "growth", item="net_profit",
        description="同比口径，分母用上期绝对值。",
    ),
    # -- 盈利能力 ----------------------------------------------------------
    "gross_margin": MetricDefinition(
        "gross_margin", "毛利率", "盈利能力",
        "毛利率 = (营业收入 - 营业成本) / 营业收入",
        StatementType.INCOME_STATEMENT, "ratio",
        numerator=("revenue",), subtract=("cost_of_revenue",), denominator=("revenue",),
    ),
    "net_margin": MetricDefinition(
        "net_margin", "净利率", "盈利能力",
        "净利率 = 净利润 / 营业收入",
        StatementType.INCOME_STATEMENT, "ratio",
        numerator=("net_profit",), denominator=("revenue",),
    ),
    "roa": MetricDefinition(
        "roa", "总资产收益率", "盈利能力",
        "ROA = 净利润 / 总资产",
        StatementType.INCOME_STATEMENT, "ratio",
        numerator=("net_profit",), denominator=("total_assets",),
        description="简化口径：分母用期末总资产（严格应为平均总资产）。",
    ),
    "roe": MetricDefinition(
        "roe", "净资产收益率", "盈利能力",
        "ROE = 净利润 / 所有者权益",
        StatementType.INCOME_STATEMENT, "ratio",
        numerator=("net_profit",), denominator=("total_equity",),
        description="简化口径：分子用含少数股东损益的净利润、分母用含少数股东权益的期末所有者权益（二者口径自洽；严格杜邦口径应为归母净利润/平均归母权益）。",
    ),
    # -- 偿债能力 ----------------------------------------------------------
    "debt_to_asset_ratio": MetricDefinition(
        "debt_to_asset_ratio", "资产负债率", "偿债能力",
        "资产负债率 = 负债合计 / 资产总计",
        StatementType.BALANCE_SHEET, "ratio",
        numerator=("total_liabilities",), denominator=("total_assets",),
    ),
    "current_ratio": MetricDefinition(
        "current_ratio", "流动比率", "偿债能力",
        "流动比率 = 流动资产合计 / 流动负债合计",
        StatementType.BALANCE_SHEET, "ratio",
        numerator=("total_current_assets",), denominator=("total_current_liabilities",),
    ),
    "quick_ratio": MetricDefinition(
        "quick_ratio", "速动比率", "偿债能力",
        "速动比率 = (流动资产合计 - 存货) / 流动负债合计",
        StatementType.BALANCE_SHEET, "ratio",
        numerator=("total_current_assets",), subtract=("inventory",),
        denominator=("total_current_liabilities",),
        description="简化口径：速动资产 = 流动资产 - 存货。",
    ),
    # -- 现金流 ------------------------------------------------------------
    "operating_cash_flow": MetricDefinition(
        "operating_cash_flow", "经营现金流净额", "现金流",
        "经营现金流净额 = 经营活动产生的现金流量净额（直接取科目值）",
        StatementType.CASH_FLOW, "raw", item="operating_cash_flow",
    ),
    "ocf_to_net_profit": MetricDefinition(
        "ocf_to_net_profit", "经营现金流/净利润比", "现金流",
        "经营现金流/净利润比 = 经营现金流净额 / 净利润",
        StatementType.CASH_FLOW, "ratio",
        numerator=("operating_cash_flow",), denominator=("net_profit",),
        description="反映盈利的现金支撑程度，>1 通常更健康。",
    ),
    # -- 运营效率 ----------------------------------------------------------
    "accounts_receivable_turnover": MetricDefinition(
        "accounts_receivable_turnover", "应收账款周转率", "运营效率",
        "应收账款周转率 = 营业收入 / 平均应收账款",
        StatementType.INCOME_STATEMENT, "turnover",
        flow_item="revenue", point_item="accounts_receivable",
        description="简化口径：分母用（期初 + 期末）应收账款 / 2，不含应收款项融资。",
    ),
    "inventory_turnover": MetricDefinition(
        "inventory_turnover", "存货周转率", "运营效率",
        "存货周转率 = 营业成本 / 平均存货",
        StatementType.INCOME_STATEMENT, "turnover",
        flow_item="cost_of_revenue", point_item="inventory",
        description="简化口径：分母用（期初 + 期末）存货 / 2。",
    ),
    # -- 财务质量（信号，非结论）------------------------------------------
    "accounts_receivable_change": MetricDefinition(
        "accounts_receivable_change", "应收账款变化", "财务质量",
        "应收账款变化 = 本期应收账款 - 上期应收账款",
        StatementType.BALANCE_SHEET, "change", item="accounts_receivable",
        description="信号：应收账款增速远超收入增速值得调查，不能直接认定造假。",
        is_signal=True,
    ),
    "inventory_change": MetricDefinition(
        "inventory_change", "存货变化", "财务质量",
        "存货变化 = 本期存货 - 上期存货",
        StatementType.BALANCE_SHEET, "change", item="inventory",
        description="信号：存货异常堆积值得调查，不能直接认定造假。",
        is_signal=True,
    ),
    "profit_cash_flow_divergence": MetricDefinition(
        "profit_cash_flow_divergence", "利润与现金流背离", "财务质量",
        "背离额 = 净利润 - 经营现金流净额",
        StatementType.INCOME_STATEMENT, "divergence",
        description="信号：背离额长期为正说明利润缺乏现金流支撑，值得调查；非结论。",
        is_signal=True,
    ),
}

# 指标组 -> 指标 code 列表（按方案第 8 章分组顺序）
GROUPS: dict[str, list[str]] = {
    "成长能力": ["revenue_growth", "net_profit_growth"],
    "盈利能力": ["gross_margin", "net_margin", "roa", "roe"],
    "偿债能力": ["debt_to_asset_ratio", "current_ratio", "quick_ratio"],
    "现金流": ["operating_cash_flow", "ocf_to_net_profit"],
    "运营效率": ["accounts_receivable_turnover", "inventory_turnover"],
    "财务质量": ["accounts_receivable_change", "inventory_change", "profit_cash_flow_divergence"],
}


# ---------------------------------------------------------------------------
# 取数与期间推断
# ---------------------------------------------------------------------------
def _pick(facts: list[FinancialFact], item: str, period: str, scope: str) -> float | None:
    """取 (standard_item, period, scope) 的标准化值；缺失返回 None。"""
    for f in facts:
        if (
            f.standard_item == item
            and f.period == period
            and f.consolidation_scope == scope
            and f.value is not None
        ):
            return f.value
    return None


def _sorted_periods(facts: list[FinancialFact], scope: str) -> list[str]:
    """按 scope 过滤，返回去重排序的期间列表。"""
    return sorted({f.period for f in facts if f.period and f.consolidation_scope == scope})


def _prev_period(period: str, periods: list[str]) -> str | None:
    """返回 periods 中 period 的前一个期间；没有则 None。"""
    if period not in periods:
        return None
    i = periods.index(period)
    return periods[i - 1] if i > 0 else None


# ---------------------------------------------------------------------------
# 计算器（按 kind 分发）
# ---------------------------------------------------------------------------
def _sum_items(
    facts: list[FinancialFact], items: tuple[str, ...], period: str, scope: str
) -> tuple[float | None, dict[str, float | None]]:
    """求一组科目之和；任一科目缺失则整体不可计算（返回 None）。"""
    vals = {i: _pick(facts, i, period, scope) for i in items}
    if any(v is None for v in vals.values()):
        return None, vals
    return sum(vals.values()), vals


def _compute(
    defn: MetricDefinition,
    facts: list[FinancialFact],
    period: str,
    scope: str,
    prev: str | None,
) -> MetricResult:
    result = MetricResult(
        code=defn.code,
        name=defn.name,
        group=defn.group,
        period=period,
        scope=scope,
        formula=defn.formula,
        is_signal=defn.is_signal,
    )

    if defn.kind == "raw":
        v = _pick(facts, defn.item, period, scope)
        result.inputs[defn.item] = v
        if v is None:
            result.status = MetricStatus.INSUFFICIENT_DATA
        else:
            result.value = v
            result.status = MetricStatus.COMPUTED
        return result

    if defn.kind == "ratio":
        num, num_vals = _sum_items(facts, defn.numerator, period, scope)
        sub, sub_vals = _sum_items(facts, defn.subtract, period, scope)
        den, den_vals = _sum_items(facts, defn.denominator, period, scope)
        result.inputs.update(num_vals)
        result.inputs.update(sub_vals)
        result.inputs.update(den_vals)
        if num is None or den is None or (defn.subtract and sub is None):
            result.status = MetricStatus.INSUFFICIENT_DATA
            return result
        numerator = num - (sub if sub is not None else 0.0)
        result.numerator = numerator
        result.denominator = den
        if den == 0:
            result.status = MetricStatus.DIVIDE_BY_ZERO
            return result
        result.value = numerator / den
        result.status = MetricStatus.COMPUTED
        return result

    if defn.kind == "growth":
        cur = _pick(facts, defn.item, period, scope)
        last = _pick(facts, defn.item, prev, scope) if prev else None
        result.inputs = {f"{defn.item}@当期": cur, f"{defn.item}@上期": last}
        if cur is None or last is None:
            result.status = MetricStatus.INSUFFICIENT_DATA
            return result
        result.numerator = cur - last
        result.denominator = abs(last)
        if last == 0:
            result.status = MetricStatus.DIVIDE_BY_ZERO
            return result
        result.value = (cur - last) / abs(last)
        result.status = MetricStatus.COMPUTED
        return result

    if defn.kind == "change":
        cur = _pick(facts, defn.item, period, scope)
        last = _pick(facts, defn.item, prev, scope) if prev else None
        result.inputs = {f"{defn.item}@当期": cur, f"{defn.item}@上期": last}
        if cur is None or last is None:
            result.status = MetricStatus.INSUFFICIENT_DATA
            return result
        result.value = cur - last
        result.status = MetricStatus.COMPUTED
        return result

    if defn.kind == "turnover":
        flow = _pick(facts, defn.flow_item, period, scope)
        point_end = _pick(facts, defn.point_item, period, scope)
        point_start = _pick(facts, defn.point_item, prev, scope) if prev else None
        result.inputs = {
            defn.flow_item: flow,
            f"{defn.point_item}@期末": point_end,
            f"{defn.point_item}@期初": point_start,
        }
        if flow is None or point_end is None or point_start is None:
            result.status = MetricStatus.INSUFFICIENT_DATA
            return result
        avg = (point_end + point_start) / 2.0
        result.numerator = flow
        result.denominator = avg
        if avg == 0:
            result.status = MetricStatus.DIVIDE_BY_ZERO
            return result
        result.value = flow / avg
        result.status = MetricStatus.COMPUTED
        return result

    if defn.kind == "divergence":
        np_ = _pick(facts, "net_profit", period, scope)
        ocf = _pick(facts, "operating_cash_flow", period, scope)
        result.inputs = {"net_profit": np_, "operating_cash_flow": ocf}
        if np_ is None or ocf is None:
            result.status = MetricStatus.INSUFFICIENT_DATA
            return result
        result.value = np_ - ocf
        result.status = MetricStatus.COMPUTED
        return result

    result.status = MetricStatus.INSUFFICIENT_DATA
    return result


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def compute_metric(
    code: str,
    facts: list[FinancialFact],
    period: str,
    scope: str = "consolidated",
) -> MetricResult:
    """计算单个指标。

    需要上期的指标（growth/change/turnover）自动从 facts 的期间序列推断上期；
    无上期则返回 insufficient_data（不编造）。
    """
    defn = METRIC_DEFINITIONS.get(code)
    if defn is None:
        raise KeyError(f"未注册的指标: {code}")
    periods = _sorted_periods(facts, scope)
    prev = _prev_period(period, periods) if defn.kind in ("growth", "change", "turnover") else None
    return _compute(defn, facts, period, scope, prev)


def compute_metrics(
    facts: list[FinancialFact],
    period: str,
    scope: str = "consolidated",
    codes: list[str] | None = None,
) -> dict[str, MetricResult]:
    """批量计算一组指标（默认全部 16 个）。"""
    selected = codes or list(METRIC_DEFINITIONS.keys())
    return {c: compute_metric(c, facts, period, scope) for c in selected}


def compute_series(
    code: str,
    facts: list[FinancialFact],
    scope: str = "consolidated",
) -> list[MetricResult]:
    """对 facts 中的所有期间，按时间顺序计算同一指标（跨期比较）。"""
    periods = _sorted_periods(facts, scope)
    return [compute_metric(code, facts, p, scope) for p in periods]
