"""validation —— 基础数据校验与质量报告。

阶段 1 基础校验：单条事实状态一致性、单位/科目映射、资产负债表平衡 sanity check。
（方案第 9 章：Sanity Check 有用但不足以证明 Agent 可靠，完整评测在 evaluation/）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from domain import DataStatus, FinancialFact, StatementType
from finance.taxonomy import UNIT_SCALE, is_mapped, normalize_name


@dataclass
class ValidationIssue:
    level: str  # error | warning
    field: str
    message: str


@dataclass
class ValidationReport:
    total: int = 0
    errors: int = 0
    warnings: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)
        if issue.level == "error":
            self.errors += 1
        else:
            self.warnings += 1
        self.total += 1

    def ok(self) -> bool:
        return self.errors == 0


def validate_fact(fact: FinancialFact) -> list[ValidationIssue]:
    """校验单条财务事实，返回问题列表。"""
    issues: list[ValidationIssue] = []

    # 值状态一致性（规则 5）
    if fact.data_status is DataStatus.PRESENT and fact.value is None:
        issues.append(
            ValidationIssue("error", "value", f"{fact.fact_id} 状态为 present 但 value 为空")
        )
    if fact.data_status is not DataStatus.PRESENT and fact.value is not None:
        issues.append(
            ValidationIssue(
                "warning",
                "data_status",
                f"{fact.fact_id} 状态为 {fact.data_status.value} 却携带 value={fact.value}",
            )
        )

    # 单位可识别（规则 1）
    if fact.unit is not None and normalize_name(fact.unit).lower() not in UNIT_SCALE:
        issues.append(
            ValidationIssue("warning", "unit", f"{fact.fact_id} 未知单位: {fact.unit}")
        )

    # 科目是否映射（规则 7）
    if fact.standard_item and not is_mapped(fact.raw_item):
        issues.append(
            ValidationIssue(
                "warning",
                "standard_item",
                f"{fact.fact_id} 科目未映射到标准科目: {fact.raw_item}",
            )
        )

    return issues


def check_balance_sheet(facts: list[FinancialFact], tolerance: float = 1e-6) -> ValidationReport:
    """资产负债表平衡校验：资产总计 ≈ 负债合计 + 所有者权益合计。

    按 (期间, 合并范围) 分组校验——真实财报含多期、合并/母公司各一套，
    不同分组不能互相覆盖。tolerance 为相对容差（浮点误差）。
    """
    report = ValidationReport()
    by_group: dict[tuple[str, str], dict[str, float]] = {}
    for f in facts:
        if f.statement_type is not StatementType.BALANCE_SHEET:
            continue
        if f.data_status is DataStatus.PRESENT and f.value is not None:
            key = (f.period or "", f.consolidation_scope)
            by_group.setdefault(key, {})[f.standard_item] = f.value

    for (period, scope), items in by_group.items():
        assets = items.get("total_assets")
        liabilities = items.get("total_liabilities")
        equity = items.get("total_equity")
        scope_label = {"parent_only": "母公司", "consolidated": "合并"}.get(scope, scope)
        label = f"期间 {period}（{scope_label}）" if period else f"（未标期间·{scope_label}）"

        missing = [
            name
            for name, val in [
                ("total_assets", assets),
                ("total_liabilities", liabilities),
                ("total_equity", equity),
            ]
            if val is None
        ]
        if missing:
            report.add(
                ValidationIssue("warning", "balance_sheet", f"{label} 缺少科目无法校验平衡: {missing}")
            )
            continue

        rhs = liabilities + equity
        diff = abs(assets - rhs)
        rel = diff / abs(assets) if assets != 0 else diff
        if rel > tolerance:
            report.add(
                ValidationIssue(
                    "error",
                    "balance_sheet",
                    f"{label} 资产负债表不平衡: 资产={assets:.2f} 负债+权益={rhs:.2f} 相对误差={rel:.2e}",
                )
            )
    return report
