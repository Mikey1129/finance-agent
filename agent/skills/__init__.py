"""agent.skills —— 可复用财务分析技能层（方案 5.7）。

Skill = 一类问题的专业方法（多工具组合），不等同于单个工具或指标函数。
第一批 5 个 Skill 静态注册于 registry.py，评测样例见 tests/test_skills.py。
"""

from .base import BaseSkill, SkillResult, SkillSection
from .registry import (
    AnnualReportSummarySkill,
    CashFlowAnalysisSkill,
    FinancialRiskAnalysisSkill,
    FinancialStatementAnalysisSkill,
    ProfitabilityAnalysisSkill,
    build_skills,
    list_skill_meta,
)

__all__ = [
    "BaseSkill",
    "SkillResult",
    "SkillSection",
    "AnnualReportSummarySkill",
    "CashFlowAnalysisSkill",
    "FinancialRiskAnalysisSkill",
    "FinancialStatementAnalysisSkill",
    "ProfitabilityAnalysisSkill",
    "build_skills",
    "list_skill_meta",
]
