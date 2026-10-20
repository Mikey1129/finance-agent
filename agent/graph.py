"""agent.graph —— LangGraph 状态图（方案 5.2 / 5.3，阶段 3）。

主工作流：resolve（意图 + 上下文消歧）→ plan（任务规划）→ execute（工具执行）
→ validate（校验 + 证据组装）→ answer（生成结果）。

第一版为确定性骨架（router / planner 均为规则版，不接 LLM），Function Calling 的
LLM 决策层阶段 4 接入。CLARIFY 意图在 resolve 后直接短路返回澄清，不走后续节点。

LangGraph 负责流程控制（方案 5.8）；具体 Hook 在 execute 节点内以 HookManager 包住
工具调用执行。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agent.checkpoint import Checkpointer
from agent.llm import LLMPlanner
from agent.context import ContextPolicy, ContextResolver
from agent.evidence import EvidenceAssembler
from agent.hooks import (
    BudgetHook,
    HookManager,
    ParamRangeHook,
    PermissionHook,
    PromptInjectionHook,
    SourceIntegrityHook,
)
from agent.planner import Planner
from agent.router import route
from agent.state import AgentState, Intent
from agent.tools import ToolResult, ToolStatus, build_tools
from auth import AccessPolicy, AuditLog, User
from finance.store import Store
from domain import AnalysisRun, EvidenceChunk, FinancialFact, ToolCallRecord


class GraphState(TypedDict, total=False):
    thread_id: str
    run_id: str
    user_query: str
    user: User | None
    company_name: str | None
    periods: list[str]
    known_companies: list[str]
    intent: str | None
    metric_codes: list[str]
    standard_items: list[str]
    plan: list[dict]
    tool_results: list[ToolResult]
    needs_clarification: bool
    clarification: str | None
    missing_evidence: list[str]
    contradictions: list[str]
    status: str
    final_answer: str | None


class AgentGraph:
    """财务分析 Agent 状态图：注入 Store / chunks，编译确定性工作流。"""

    def __init__(
        self,
        store: Store,
        chunks: list[EvidenceChunk] | None = None,
        checkpointer: Checkpointer | None = None,
        record_runs: bool = False,
        rule_version: str = "deterministic-v1",
        llm_planner: LLMPlanner | None = None,
        access_policy: AccessPolicy | None = None,
        audit_log: AuditLog | None = None,
    ):
        self.store = store
        self.chunks = chunks or []
        self.checkpointer = checkpointer
        self.record_runs = record_runs
        self.rule_version = rule_version
        self.llm_planner = llm_planner
        self.access_policy = access_policy
        self.audit_log = audit_log
        self.registry = build_tools(store, self.chunks)
        self.hooks = HookManager(
            [
                ParamRangeHook(),
                PermissionHook(access_policy),
                BudgetHook(),
                SourceIntegrityHook(),
                PromptInjectionHook(),
            ]
        )
        self.resolver = ContextResolver()
        self.planner = Planner()
        self.assembler = EvidenceAssembler()
        self.context_policy = ContextPolicy()
        self.graph = self._build()

    # -- 节点 ---------------------------------------------------------------
    def _resolve(self, state: GraphState) -> dict:
        query = state["user_query"]
        base = AgentState(
            thread_id=state.get("thread_id", "t"),
            run_id=state.get("run_id", "r"),
            user_query=query,
            company_name=state.get("company_name"),
            periods=state.get("periods", []),
        )
        known = state.get("known_companies") or (
            [state["company_name"]] if state.get("company_name") else None
        )
        rc = self.resolver.resolve(query, base, known_companies=known)
        r = route(query, known_companies=known)
        result = {
            "company_name": rc.company_name,
            "periods": rc.periods,
            "intent": rc.intent.value if rc.intent else None,
            "metric_codes": r.metric_codes,
            "standard_items": r.standard_items,
            "needs_clarification": rc.needs_clarification,
            "clarification": rc.clarification,
        }
        if rc.needs_clarification:
            # CLARIFY 短路到 END，不经过 answer 节点，此处直接给出状态与答案
            result["status"] = "need_clarification"
            result["final_answer"] = rc.clarification
        return result

    def _plan(self, state: GraphState) -> dict:
        intent = Intent(state["intent"]) if state.get("intent") else None
        # 深度归因（路径 C）启用 LLM Function Calling 动态规划；其余走规则 Planner。
        if self.llm_planner and intent is Intent.ATTRIBUTION:
            calls = self.llm_planner.plan(
                state["user_query"],
                self.registry.schemas(),
                company_name=state.get("company_name"),
                periods=state.get("periods", []),
            )
            if calls:
                return {"plan": [{"tool": c.tool, "args": c.args} for c in calls]}
        steps = self.planner.plan(
            intent,
            query=state["user_query"],
            company_name=state.get("company_name"),
            periods=state.get("periods", []),
            metric_codes=state.get("metric_codes", []),
            standard_items=state.get("standard_items", []),
        )
        return {"plan": [{"tool": s.tool, "args": s.args} for s in steps]}

    def _execute(self, state: GraphState) -> dict:
        results: list[ToolResult] = []
        user = state.get("user")
        for step in state.get("plan", []):
            results.append(self.hooks.invoke(self.registry, step["tool"], user=user, **step["args"]))
        return {"tool_results": results}

    def _validate(self, state: GraphState) -> dict:
        facts: list[FinancialFact] = []
        metrics: dict[str, Any] = {}
        chunks: list[EvidenceChunk] = []
        for r in state.get("tool_results", []):
            if r.tool == "query_financial_fact" and isinstance(r.data, list):
                facts.extend(r.data)
            elif r.tool == "compute_metric" and isinstance(r.data, dict):
                metrics.update(r.data)
            elif r.tool == "retrieve_evidence" and isinstance(r.data, list):
                chunks.extend(r.data)

        intent = Intent(state["intent"]) if state.get("intent") else None
        pack = self.assembler.assemble(
            state["user_query"], intent=intent,
            company_name=state.get("company_name"), periods=state.get("periods", []),
            facts=facts, metrics=metrics, chunks=chunks,
        )
        return {
            "missing_evidence": pack.missing_evidence,
            "contradictions": pack.contradictions,
        }

    def _answer(self, state: GraphState) -> dict:
        if state.get("needs_clarification"):
            return {"final_answer": state.get("clarification"), "status": "need_clarification"}
        parts = [self._summarize(r) for r in state.get("tool_results", []) if r.status is ToolStatus.SUCCESS]
        if state.get("missing_evidence"):
            parts.append("⚠ 缺失证据: " + "；".join(state["missing_evidence"]))
        if state.get("contradictions"):
            parts.append("⚠ 发现冲突: " + "；".join(state["contradictions"]))
        return {"final_answer": "\n".join(parts) or "未获取到结果", "status": "success"}

    @staticmethod
    def _summarize(r: ToolResult) -> str:
        if r.tool == "query_financial_fact":
            rows = [f"{f.standard_item}={f.value} 元" for f in r.data if f.value is not None]
            return "财务事实: " + "; ".join(rows) if rows else "财务事实: 无"
        if r.tool == "compute_metric":
            rows = [f"{m.name}={m.value:.4f}" for m in r.data.values() if m.computed]
            return "指标: " + "; ".join(rows) if rows else "指标: 无（数据不足）"
        if r.tool == "retrieve_evidence":
            return f"检索到 {len(r.data)} 条文本证据"
        if r.tool == "validate_result":
            return f"校验: errors={r.data.errors} warnings={r.data.warnings}"
        return f"{r.tool}: {r.status.value}"

    # -- 路由 ---------------------------------------------------------------
    @staticmethod
    def _route_after_resolve(state: GraphState) -> str:
        return "clarify" if state.get("needs_clarification") else "plan"

    # -- 编译 ---------------------------------------------------------------
    def _build(self):
        g = StateGraph(GraphState)
        g.add_node("resolve", self._resolve)
        g.add_node("plan", self._plan)
        g.add_node("execute", self._execute)
        g.add_node("validate", self._validate)
        g.add_node("answer", self._answer)

        g.add_edge(START, "resolve")
        g.add_conditional_edges("resolve", self._route_after_resolve, {"clarify": END, "plan": "plan"})
        g.add_edge("plan", "execute")
        g.add_edge("execute", "validate")
        g.add_edge("validate", "answer")
        g.add_edge("answer", END)
        return g.compile()

    def run(
        self,
        query: str,
        company_name: str | None = None,
        periods: list[str] | None = None,
        known_companies: list[str] | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
        user: User | None = None,
    ) -> GraphState:
        thread_id = thread_id or "default"
        run_id = run_id or uuid.uuid4().hex[:12]

        # 多轮续接：从 checkpoint 继承上一轮的上下文（公司/期间/意图）
        prev = self.checkpointer.load(thread_id) if self.checkpointer else None
        if prev:
            company_name = company_name or prev.get("company_name")
            periods = periods or prev.get("periods", [])
            known_companies = known_companies or prev.get("known_companies")

        initial: GraphState = {
            "thread_id": thread_id,
            "run_id": run_id,
            "user_query": query,
            "user": user,
            "company_name": company_name,
            "periods": periods or [],
            "known_companies": known_companies or [],
        }
        out = self.graph.invoke(initial)

        # 落库运行记录（可回放审计）
        if self.record_runs:
            self.store.insert_analysis_run(self._build_run(run_id, thread_id, out))

        # 审计日志（阶段 5）：记录提问与权限拒绝
        self._audit(out, user)

        # 保存会话快照（供下一轮多轮续接）
        if self.checkpointer:
            self.checkpointer.save(
                thread_id,
                {
                    "company_name": out.get("company_name"),
                    "periods": out.get("periods", []),
                    "intent": out.get("intent"),
                    "known_companies": known_companies or [],
                },
            )

        return out

    def _audit(self, state: GraphState, user: User | None) -> None:
        """记录审计事件（阶段 5）：每次提问 + 权限拒绝。无 audit_log 时为 no-op。"""
        if self.audit_log is None:
            return
        uid = user.user_id if user else None
        self.audit_log.record(
            "ask", user_id=uid, resource_type="company",
            resource_id=state.get("company_name"), result=state.get("status", "success"),
        )
        for r in state.get("tool_results", []):
            if (
                r.error
                and r.error.type == "hook_blocked"
                and r.error.message
                and "无权访问" in r.error.message
            ):
                self.audit_log.record(
                    "access_denied", user_id=uid, resource_type="company",
                    result="denied", detail=r.error.message,
                )

    def _build_run(self, run_id: str, thread_id: str, state: GraphState) -> AnalysisRun:
        """从图执行结果构造 AnalysisRun 运行记录（可回放）。"""
        tool_calls = [
            ToolCallRecord(
                tool=r.tool,
                status=r.status.value,
                summary=self._summarize(r),
                source_refs=[f"{s.kind}:{s.ref_id}" for s in r.sources],
            )
            for r in state.get("tool_results", [])
        ]
        validation = list(state.get("contradictions", []))
        if state.get("missing_evidence"):
            validation.append("缺失证据: " + "；".join(state["missing_evidence"]))
        return AnalysisRun(
            run_id=run_id,
            thread_id=thread_id,
            user_id=state.get("user").user_id if state.get("user") else None,
            user_query=state["user_query"],
            company_name=state.get("company_name"),
            periods=state.get("periods", []),
            intent=state.get("intent"),
            status=state.get("status", "success"),
            plan=state.get("plan", []),
            tool_calls=tool_calls,
            validation_summary=validation,
            final_answer=state.get("final_answer"),
            rule_version=self.rule_version,
            finished_at=datetime.now(),
        )
