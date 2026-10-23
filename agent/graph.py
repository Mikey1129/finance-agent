"""agent.graph —— LangGraph 状态图（方案 5.2 / 5.3，阶段 3）。

主工作流：resolve（意图 + 上下文消歧）→ plan（任务规划）→ execute（工具执行）
→ validate（校验 + 证据组装）→ answer（生成结果）。

第一版为确定性骨架（router / planner 均为规则版，不接 LLM），Function Calling 的
LLM 决策层阶段 4 接入。CLARIFY 意图在 resolve 后直接短路返回澄清，不走后续节点。

LangGraph 负责流程控制（方案 5.8）；具体 Hook 在 execute 节点内以 HookManager 包住
工具调用执行。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from config import rule_version as _cfg_rule_version
from agent.attribution import AttributionWriter, render_report
from agent.checkpoint import Checkpointer
from agent.llm import LLMPlanner
from agent.context import ContextPolicy, ContextResolver
from agent.evidence import EvidenceAssembler, EvidencePack
from agent.hooks import (
    BudgetHook,
    HookManager,
    ParamRangeHook,
    PermissionHook,
    PromptInjectionHook,
    SourceIntegrityHook,
)
from agent.planner import ComplexityLevel, Planner, assess_complexity
from agent.reviewer import ConclusionReviewer
from agent.router import route
from agent.state import AgentState, Intent
from agent.tools import SourceRef, ToolResult, ToolStatus, build_tools
from auth import AccessPolicy, AuditLog, User
from finance.metrics import METRIC_DEFINITIONS
from finance.store import Store
from finance.taxonomy import item_zh
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
    evidence_pack: EvidencePack | None
    answer_sources: list[SourceRef]
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
        rule_version: str | None = None,
        llm_planner: LLMPlanner | None = None,
        attribution_writer: AttributionWriter | None = None,
        reviewer: ConclusionReviewer | None = None,
        access_policy: AccessPolicy | None = None,
        audit_log: AuditLog | None = None,
    ):
        self.store = store
        self.chunks = chunks or []
        self.checkpointer = checkpointer
        self.record_runs = record_runs
        self.rule_version = rule_version or _cfg_rule_version()
        self.llm_planner = llm_planner
        self.attribution_writer = attribution_writer
        self.reviewer = reviewer
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

        # LLM 兜底：规则未命中（CLARIFY）但已确定公司时，交给 LLM 理解并规划工具，
        # 让规则枚举不到的口语问法也能落到正确工具上（数字仍由工具/数据层产生）。
        if rc.intent is Intent.CLARIFY and rc.company_name and self.llm_planner:
            fallback = self._llm_fallback(query, rc.company_name, rc.periods)
            if fallback is not None:
                result["intent"] = fallback["intent"]
                result["plan"] = fallback["plan"]
                result["periods"] = fallback["periods"]
                result["needs_clarification"] = False
                result["clarification"] = None

        if result["needs_clarification"]:
            # CLARIFY 短路到 END，不经过 answer 节点，此处直接给出状态与答案
            result["status"] = "need_clarification"
            result["final_answer"] = result["clarification"]
        return result

    def _latest_period(self, company_name: str) -> str | None:
        """该公司入库的最新期间（按 period 字典序取最大，与 metrics._sorted_periods 一致），无数据返回 None。"""
        rows = self.store.query_facts(company_name=company_name)
        periods = sorted({r["period"] for r in rows if r.get("period")})
        return periods[-1] if periods else None

    def _llm_fallback(self, query: str, company_name: str, periods: list[str]) -> dict | None:
        """规则未命中（CLARIFY）但已确定公司时，用 LLM 理解并规划工具调用。

        返回 {intent, plan, periods}；LLM 未给出可用工具调用时返回 None（维持规则澄清）。
        数字仍由工具/数据层产生，LLM 只决定「调用什么工具、传什么参数」。
        """
        if not self.llm_planner:
            return None
        try:
            calls = self.llm_planner.plan(
                query, self.registry.schemas(), company_name=company_name, periods=periods
            )
        except Exception:
            return None
        if not calls:
            return None

        # 期间兜底：无期间时用最新可用期间，并把年份注入需要 fiscal_year 的工具
        resolved_periods = list(periods or [])
        if not resolved_periods:
            latest = self._latest_period(company_name)
            if latest:
                resolved_periods = [latest]
        fiscal_year = None
        if resolved_periods:
            m = re.search(r"(\d{4})", str(resolved_periods[-1]))
            fiscal_year = int(m.group(1)) if m else None

        plan: list[dict] = []
        tools: set[str] = set()
        for c in calls:
            tool = self.registry.get(c.tool)
            if tool is None:
                continue  # 未知工具直接丢弃（execute 也会 fail）
            args = dict(c.args or {})
            fields = tool.input_schema.model_fields
            # 强制以规则消歧出的公司为准（LLM 可能填错/漏填公司名）
            if "company_name" in fields:
                args["company_name"] = company_name
            # 缺年份时用最新可用年份兜底（compute_metric 等需要 fiscal_year）
            if "fiscal_year" in fields and args.get("fiscal_year") is None and fiscal_year is not None:
                args["fiscal_year"] = fiscal_year
            plan.append({"tool": c.tool, "args": args})
            tools.add(c.tool)

        if not plan:
            return None

        # 从工具名推断意图（供 evidence 组装与 answer 分支）
        if tools & {"retrieve_evidence", "validate_result"}:
            intent = Intent.ATTRIBUTION
        elif tools & {"compute_metric", "compare_periods", "compare_companies"}:
            intent = Intent.METRIC_ANALYSIS
        else:
            intent = Intent.FACT_QUERY

        return {"intent": intent.value, "plan": plan, "periods": resolved_periods}

    def _plan(self, state: GraphState) -> dict:
        # LLM 兜底已在 resolve 阶段产出 plan，直接沿用，跳过规则/LLM 规划
        if state.get("plan"):
            return {"periods": state.get("periods", [])}

        intent = Intent(state["intent"]) if state.get("intent") else None
        company = state.get("company_name")
        periods = list(state.get("periods", []) or [])
        # 未指定期间时默认用该公司最新可用期间（否则 compute_metric 收到空年度会失败）
        if not periods and company:
            latest = self._latest_period(company)
            if latest:
                periods = [latest]
        # 复杂度门控：仅深度归因（COMPLEX）且注入 LLM 时走动态规划；其余/失败走规则 Planner。
        if self.llm_planner and assess_complexity(intent, state["user_query"]) is ComplexityLevel.COMPLEX:
            calls = self.llm_planner.plan(
                state["user_query"],
                self.registry.schemas(),
                company_name=company,
                periods=periods,
            )
            if calls:
                return {"plan": [{"tool": c.tool, "args": c.args} for c in calls], "periods": periods}
        steps = self.planner.plan(
            intent,
            query=state["user_query"],
            company_name=company,
            periods=periods,
            metric_codes=state.get("metric_codes", []),
            standard_items=state.get("standard_items", []),
        )
        return {"plan": [{"tool": s.tool, "args": s.args} for s in steps], "periods": periods}

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
            "evidence_pack": pack,
            "missing_evidence": pack.missing_evidence,
            "contradictions": pack.contradictions,
        }

    def _answer(self, state: GraphState) -> dict:
        if state.get("needs_clarification"):
            return {"final_answer": state.get("clarification"), "status": "need_clarification"}

        # 深度归因走 LLM 结论合成（带证据等级）；未注入/失败降级模板拼接。
        intent = Intent(state["intent"]) if state.get("intent") else None
        pack = state.get("evidence_pack")
        if (
            self.attribution_writer is not None
            and intent is Intent.ATTRIBUTION
            and pack is not None
        ):
            draft = self.attribution_writer.write(pack, self.context_policy)
            if draft is not None:
                # 输出前校验 gate：reviewer 批判性审查初稿，返回修正版；失败降级用初稿。
                if self.reviewer is not None:
                    reviewed = self.reviewer.review(draft, pack, self.context_policy)
                    if reviewed is not None:
                        draft = reviewed
                report, sources = render_report(draft)
                return {
                    "final_answer": report,
                    "answer_sources": sources,
                    "status": "success",
                }

        # 指标问法（METRIC_ANALYSIS）只汇总指标计算结果，跳过 query_financial_fact 的
        # 原始事实（否则全部事实被列出，真正的指标结论被挤到末尾）。
        results = state.get("tool_results", [])
        if intent is Intent.METRIC_ANALYSIS:
            metric_tools = {"compute_metric", "compare_periods", "compare_companies"}
            parts = [
                self._summarize(r)
                for r in results
                if r.status is ToolStatus.SUCCESS and r.tool in metric_tools
            ]
        else:
            parts = [self._summarize(r) for r in results if r.status is ToolStatus.SUCCESS]
        if state.get("missing_evidence"):
            parts.append("⚠ 缺失证据: " + "；".join(state["missing_evidence"]))
        if state.get("contradictions"):
            parts.append("⚠ 发现冲突: " + "；".join(state["contradictions"]))
        return {"final_answer": "\n".join(parts) or "未获取到结果", "status": "success"}

    @staticmethod
    def _summarize(r: ToolResult) -> str:
        # 工具失败（data=None）在 _build_run 里也会被总结，统一兜底，避免 None 解引用崩溃
        if r.data is None:
            return f"{r.tool}: {r.status.value}"
        if r.tool == "query_financial_fact":
            rows = []
            for f in r.data:
                if f.value is None:
                    continue
                zh = item_zh(f.standard_item)
                year = ""
                if f.period:
                    m = re.search(r"(\d{4})", str(f.period))
                    if m:
                        year = f"（{m.group(1)}年）"
                rows.append(f"{zh}{year} = {f.value:,.2f} 元")
            return "；".join(rows) if rows else "未查到财务事实"
        if r.tool == "compute_metric":
            rows = []
            for m in r.data.values():
                if not m.computed:
                    continue
                defn = METRIC_DEFINITIONS.get(m.code)
                if defn is not None and defn.is_percent:
                    rows.append(f"{m.name}={m.value:.2%}")
                else:
                    rows.append(f"{m.name}={m.value:,.2f}")
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
