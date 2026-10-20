"""agent.skills.base —— Skill 基类与输出结构（方案 5.7）。

Skill 是「可复用业务流程」，不等同于单个工具或指标函数：它把多个工具
（查事实/算指标/检索证据/校验）按固定流程组合，产出结构化的分析结论。

每个 Skill 至少包含（方案 5.7 硬要求）：
  适用范围（applicable）/ 输入（run 参数）/ 执行流程（run 实现）/
  依赖工具（_invoke 调用的工具）/ 输出 Schema（SkillResult）/
  校验规则（各 skill 内的 sanity 检查 → warnings）/ 评测样例（tests/test_skills.py）。

Skill 复用工具层与 Hook 三层校验（入口参数/预算/来源完整性），不绕过安全网。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from agent.hooks import (
    BudgetHook,
    HookManager,
    ParamRangeHook,
    PromptInjectionHook,
    SourceIntegrityHook,
)
from agent.tools import ToolResult, ToolStatus, build_tools


class SkillSection(BaseModel):
    """Skill 结果的一个分节（指标卡片 / 事实 / 趋势 / 信号 / 证据）。"""

    heading: str
    kind: str = Field(description="metrics | facts | signals | evidence | validation")
    rows: list[dict] = Field(default_factory=list)


class SkillResult(BaseModel):
    """Skill 统一输出：结论摘要 + 分节结构化数据 + 来源引用 + 校验警告。"""

    skill: str
    name: str
    summary: str
    sections: list[SkillSection] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: str = "success"  # success | insufficient


def _sources_of(results: list[ToolResult]) -> list[dict]:
    """聚合多个工具结果的来源引用（去重，按 kind+ref_id）。"""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for r in results:
        for s in r.sources:
            key = (s.kind, s.ref_id)
            if key in seen:
                continue
            seen.add(key)
            out.append(s.model_dump())
    return out


class BaseSkill(ABC):
    """Skill 基类：绑定 Store + chunks，提供受 Hook 保护的工具调用。"""

    code: str = ""
    name: str = ""
    description: str = ""
    applicable: str = ""

    def __init__(self, store, chunks=None):
        self.store = store
        self.chunks = chunks or []
        self.registry = build_tools(store, self.chunks)
        self.hooks = HookManager(
            [ParamRangeHook(), BudgetHook(), SourceIntegrityHook(), PromptInjectionHook()]
        )

    def _invoke(self, tool: str, **args) -> ToolResult:
        """走 Hook 三层校验地调用工具（与 AgentGraph 一致）。"""
        return self.hooks.invoke(self.registry, tool, **args)

    def _facts(self, company_name: str, fiscal_year: int | None = None) -> list:
        """查某公司（某年度）的全部财务事实。"""
        r = self._invoke(
            "query_financial_fact",
            company_name=company_name,
            fiscal_year=fiscal_year,
        )
        return r.data if r.status is ToolStatus.SUCCESS else []

    def _metrics(
        self, company_name: str, fiscal_year: int, codes: list[str]
    ) -> dict:
        """计算一组指标，返回 {code: MetricResult}；无数据返回 {}。"""
        r = self._invoke(
            "compute_metric",
            company_name=company_name,
            fiscal_year=fiscal_year,
            metric_codes=codes,
        )
        return r.data if r.status is ToolStatus.SUCCESS else {}

    def _evidence(
        self, query: str, company_name: str, fiscal_year: int | None = None, top_k: int = 5
    ) -> list:
        """检索文本证据（无 chunks 时返回空列表）。"""
        r = self._invoke(
            "retrieve_evidence",
            query=query,
            company_name=company_name,
            fiscal_year=fiscal_year,
            top_k=top_k,
        )
        return r.data if r.status is ToolStatus.SUCCESS else []

    @abstractmethod
    def run(self, company_name: str, fiscal_year: int | None = None) -> SkillResult:
        """执行技能，产出结构化结论。"""
