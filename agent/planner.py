"""agent.planner —— 任务规划（方案 5.2 / 5.3，仅复杂任务）。

简单查询（路径 A）不做完整规划（查营收直接走 SQL，不进 Planner、不进 RAG）。
第一版按意图用规则生成工具调用序列，限定工具白名单、最大步数（重试预算由
agent.hooks.BudgetHook 在调用层限制）。

三条路径的步骤：
  A 精确数值查询 → query_financial_fact
  B 指标趋势分析 → 多期用 compare_periods；单期 query + compute_metric
  C 深度归因分析 → query → compute_metric → retrieve_evidence → validate_result
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from agent.state import Intent


class ComplexityLevel(str, Enum):
    """任务复杂度（方案 5.2「LLM 决策边界」的显式门控）。"""

    SIMPLE = "simple"    # 规则 Planner，零 LLM
    COMPLEX = "complex"  # 走 llm_planner 动态规划（+ LLM 结论合成）


def assess_complexity(intent: Intent | None, query: str = "") -> ComplexityLevel:
    """按任务复杂度决定是否启用 LLM：简单问题（查数值/算指标/澄清）走规则零 LLM，
    深度归因（需动态检索 + 多工具 + 组织结论）才判复杂、走 LLM。

    当前复杂度 = 意图级。未来可在 ATTRIBUTION 内部按「多指标联动/多期间/需多步检索」
    再细分为二级复杂度（留此扩展点）。
    """
    if intent is Intent.ATTRIBUTION:
        return ComplexityLevel.COMPLEX
    return ComplexityLevel.SIMPLE


@dataclass
class PlanStep:
    """一步工具调用。"""

    tool: str
    args: dict = field(default_factory=dict)


def _year(period: str) -> int | None:
    """从期间标识提取年份（'2024' / '2024-12-31' → 2024）。"""
    m = re.search(r"(\d{4})", period)
    return int(m.group(1)) if m else None


class Planner:
    """规则版任务规划器：按意图生成工具调用序列。"""

    MAX_STEPS = 5

    def plan(
        self,
        intent: Intent | None,
        query: str = "",
        company_name: str | None = None,
        periods: list[str] | None = None,
        metric_codes: list[str] | None = None,
        standard_items: list[str] | None = None,
    ) -> list[PlanStep]:
        periods = periods or []
        company = company_name or ""
        fiscal_year = _year(periods[-1]) if periods else None

        if intent is Intent.FACT_QUERY:
            step_period = periods[-1] if periods else None
            steps = [
                PlanStep(
                    "query_financial_fact",
                    {"company_name": company, "fiscal_year": fiscal_year, "period": step_period, "standard_item": item},
                )
                for item in (standard_items or [])
            ]
            return steps[: self.MAX_STEPS]

        if intent is Intent.METRIC_ANALYSIS:
            codes = metric_codes or []
            if len(periods) > 1:
                return [
                    PlanStep("compare_periods", {"company_name": company, "metric_codes": codes})
                ]
            return [
                PlanStep(
                    "query_financial_fact",
                    {"company_name": company, "fiscal_year": fiscal_year},
                ),
                PlanStep(
                    "compute_metric",
                    {"company_name": company, "fiscal_year": fiscal_year, "metric_codes": codes},
                ),
            ][: self.MAX_STEPS]

        if intent is Intent.ATTRIBUTION:
            steps = [
                PlanStep(
                    "query_financial_fact",
                    {"company_name": company, "fiscal_year": fiscal_year},
                ),
                PlanStep(
                    "compute_metric",
                    {"company_name": company, "fiscal_year": fiscal_year, "metric_codes": metric_codes or []},
                ),
                PlanStep(
                    "retrieve_evidence",
                    {"query": query, "company_name": company},
                ),
                PlanStep(
                    "validate_result",
                    {"company_name": company, "fiscal_year": fiscal_year},
                ),
            ]
            return steps[: self.MAX_STEPS]

        return []  # CLARIFY 或未知意图不规划
