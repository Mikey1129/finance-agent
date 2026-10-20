"""agent.context —— 上下文管理（方案 5.5）。

核心原则：AgentState ≠ LLM 上下文。State 保存完整任务状态，但每次调 LLM 只发送
当前步骤需要的子集。三个组件：

- ContextPolicy：控制 Token 预算、保留优先级、证据数量、历史轮数（可配置，不写死）。
- ContextResolver：多轮追问消歧——把「那 2024 年呢？」「为什么下降了？」解析成
  明确的公司/报告/期间/分析目标；当前提问与之前公司冲突时以新指令为准。
- ContextBuilder：每次调 LLM 前统一组装上下文，按优先级裁剪到预算。

裁剪优先级（方案 5.5）：System Prompt 与业务上下文必保留；优先保留报告 ID、期间、
单位、口径、已验证数值、来源引用和待解决问题；证据片段只注入筛选后的；无关工具
输出不进入上下文。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from agent.router import route
from agent.state import AgentState, Intent
from agent.tools import ToolResult
from domain import EvidenceChunk


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：中文 1 字约 1 token，统一按 len//2 保守估计。"""
    return max(1, len(text) // 2)


@dataclass
class ContextPolicy:
    """上下文预算配置（方案 5.5 Token 预算可配置，不写死一个数字）。"""

    max_tokens: int = 4000
    max_evidence_chunks: int = 5
    max_tool_results: int = 5
    max_history_turns: int = 3


class ResolvedContext(BaseModel):
    """多轮消歧后的明确分析目标。"""

    company_name: str | None = None
    periods: list[str] = field(default_factory=list)
    intent: Intent | None = None
    needs_clarification: bool = False
    clarification: str | None = None


class ContextResolver:
    """多轮追问消歧（方案 5.5 ContextResolver）。"""

    def resolve(
        self,
        query: str,
        state: AgentState,
        known_companies: list[str] | None = None,
    ) -> ResolvedContext:
        if known_companies is None:
            known_companies = [state.company_name] if state.company_name else None
        r = route(query, known_companies=known_companies)

        # 公司消歧：新指令明确公司则以新指令为准；否则继承 state 中的公司
        company = r.company_name or state.company_name

        # 期间消歧：新指令有年份用新年份；「近 N 年」从 state 期间取最近 N 期；否则继承
        periods = r.periods if r.periods else list(state.periods)
        if r.year_span and not r.periods and state.periods:
            periods = list(state.periods[-r.year_span :])

        needs_clarification = False
        clarification = None
        if r.intent is Intent.CLARIFY:
            needs_clarification = True
            clarification = "无法理解问题，请补充公司/期间/指标"
        elif company is None:
            needs_clarification = True
            clarification = "请提供要分析的公司名称"

        return ResolvedContext(
            company_name=company,
            periods=periods,
            intent=r.intent,
            needs_clarification=needs_clarification,
            clarification=clarification,
        )


class ContextPayload(BaseModel):
    """组装后的 LLM 上下文（结构化，非拼好的 prompt 字符串）。"""

    system: str = ""
    company_name: str | None = None
    periods: list[str] = field(default_factory=list)
    intent: Intent | None = None
    tool_results: list[ToolResult] = field(default_factory=list)
    evidence_chunks: list[EvidenceChunk] = field(default_factory=list)
    history: list[str] = field(default_factory=list)
    estimated_tokens: int = 0

    def within_budget(self, policy: ContextPolicy) -> bool:
        return self.estimated_tokens <= policy.max_tokens


class ContextBuilder:
    """每次调 LLM 前统一组装上下文，按优先级裁剪到预算。"""

    def __init__(self, policy: ContextPolicy | None = None):
        self.policy = policy or ContextPolicy()

    def build(
        self,
        state: AgentState,
        resolved: ResolvedContext,
        evidence: list[EvidenceChunk] | None = None,
        history: list[str] | None = None,
    ) -> ContextPayload:
        evidence = evidence or []
        history = history or []

        # 1. 必保留：System Prompt + 业务上下文（公司/期间/意图）
        system = "你是企业财报分析 Agent。财务数字必须由数据层与计算层产生，结论必须带来源。"
        business_tokens = estimate_tokens(
            f"{resolved.company_name or ''} {resolved.periods or ''} {resolved.intent or ''}"
        )

        # 2. 工具结果：只保留最近 N 条（无关输出不进入上下文）
        tool_results = state.tool_results[-self.policy.max_tool_results :]

        # 3. 证据片段：只注入筛选排序去重后的前 N 条
        evidence_chunks = evidence[: self.policy.max_evidence_chunks]

        # 4. 历史对话：只保留最近 N 轮原始对话
        recent_history = history[-self.policy.max_history_turns :]

        payload = ContextPayload(
            system=system,
            company_name=resolved.company_name,
            periods=resolved.periods,
            intent=resolved.intent,
            tool_results=tool_results,
            evidence_chunks=evidence_chunks,
            history=recent_history,
            estimated_tokens=0,
        )
        payload.estimated_tokens = self._estimate(payload, business_tokens)

        # 5. 超预算则按优先级裁剪：先砍历史，再砍证据片段
        while not payload.within_budget(self.policy) and (payload.history or payload.evidence_chunks):
            if payload.history:
                payload.history = payload.history[1:]  # 丢最旧
            elif payload.evidence_chunks:
                payload.evidence_chunks = payload.evidence_chunks[:-1]  # 丢尾部
            payload.estimated_tokens = self._estimate(payload, business_tokens)

        return payload

    @staticmethod
    def _estimate(payload: ContextPayload, business_tokens: int) -> int:
        total = estimate_tokens(payload.system) + business_tokens
        for r in payload.tool_results:
            total += estimate_tokens(f"{r.tool} {r.status.value} {r.error.message if r.error else ''}")
        for c in payload.evidence_chunks:
            total += estimate_tokens(c.text)
        for h in payload.history:
            total += estimate_tokens(h)
        return total
