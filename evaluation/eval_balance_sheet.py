"""eval_balance_sheet —— 评测 A：整表级（资产负债表平衡）。

分层评测原则（方案第 9 章）：字段准确率 99% ≠ 整表正确。
本模块对一份真实财报的整张资产负债表做平衡校验——资产总计 ≈ 负债合计 + 所有者权益合计，
逐期间、逐合并范围验证，是「整表级」正确性的确定性锚点。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from domain import DataStatus, StatementType
from finance.validation import check_balance_sheet
from ingestion.pdf_parser import parse_pdf

# 资产负债表平衡三要素
_BALANCE_ITEMS = ("total_assets", "total_liabilities", "total_equity")


@dataclass
class PeriodBalance:
    period: str
    scope: str
    assets: float | None = None
    liabilities: float | None = None
    equity: float | None = None

    @property
    def complete(self) -> bool:
        return all(v is not None for v in (self.assets, self.liabilities, self.equity))

    @property
    def balanced(self) -> bool:
        if not self.complete:
            return False
        rhs = self.liabilities + self.equity
        diff = abs(self.assets - rhs)
        rel = diff / abs(self.assets) if self.assets else diff
        return rel <= 1e-6


@dataclass
class BalanceSheetEvalResult:
    company_name: str
    fiscal_year: int
    periods: list[PeriodBalance] = field(default_factory=list)
    total_errors: int = 0
    total_warnings: int = 0

    @property
    def passed(self) -> bool:
        """整表级通过：无不平衡（error），且至少有一期完整且平衡。"""
        return self.total_errors == 0 and any(p.complete and p.balanced for p in self.periods)


def eval_balance_sheet(
    path: str,
    company_name: str,
    fiscal_year: int,
    scope: str | None = None,
) -> BalanceSheetEvalResult:
    """解析财报 PDF，按 (期间, 合并范围) 校验整张资产负债表是否平衡。

    scope 传 None 表示校验所有合并范围；传 'consolidated' / 'parent_only' 只校验其一。
    """
    result = BalanceSheetEvalResult(company_name=company_name, fiscal_year=fiscal_year)
    parsed = parse_pdf(path, company_name, fiscal_year, StatementType.BALANCE_SHEET)

    # 汇总三要素
    by_group: dict[tuple[str, str], dict[str, float]] = {}
    for f in parsed.facts:
        if f.standard_item not in _BALANCE_ITEMS:
            continue
        if f.data_status is DataStatus.PRESENT and f.value is not None:
            key = (f.period or "", f.consolidation_scope)
            by_group.setdefault(key, {})[f.standard_item] = f.value

    for (period, group_scope), items in sorted(by_group.items(), key=lambda kv: str(kv[0][0])):
        if scope is not None and group_scope != scope:
            continue
        result.periods.append(
            PeriodBalance(
                period=period,
                scope=group_scope,
                assets=items.get("total_assets"),
                liabilities=items.get("total_liabilities"),
                equity=items.get("total_equity"),
            )
        )

    # 沿用基础校验的错误/警告计数（含缺失科目、不平衡错误）
    report = check_balance_sheet(parsed.facts)
    result.total_errors = report.errors
    result.total_warnings = report.warnings
    return result
