"""eval_metrics —— 评测 B：指标计算（真实财报端到端锚定）。

分层评测原则（方案第 9 章）：指标级评测独立于字段级/整表级。
用真实财报跑全部指标，断言关键盈利/偿债指标落在合理区间，防止科目映射污染
（如「营业总成本」误当「营业成本」）导致指标静默跑偏。精确公式与边界在 tests/test_metrics.py 覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass

from domain import StatementType
from finance.metrics import MetricStatus, compute_metrics
from ingestion.pdf_parser import parse_pdf


@dataclass
class MetricsEvalResult:
    company_name: str
    fiscal_year: int
    scope: str
    metrics: dict  # code -> MetricResult

    @property
    def computed_count(self) -> int:
        return sum(1 for m in self.metrics.values() if m.computed)

    @property
    def total(self) -> int:
        return len(self.metrics)


def eval_metrics(
    path: str,
    company_name: str,
    fiscal_year: int,
    scope: str = "consolidated",
) -> MetricsEvalResult:
    """解析三张报表、合并事实，对指定期间/范围计算全部指标。"""
    facts = []
    for st in (StatementType.BALANCE_SHEET, StatementType.INCOME_STATEMENT, StatementType.CASH_FLOW):
        facts.extend(parse_pdf(path, company_name, fiscal_year, st).facts)
    metrics = compute_metrics(facts, str(fiscal_year), scope)
    return MetricsEvalResult(company_name, fiscal_year, scope, metrics)
