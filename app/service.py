"""app.service —— 财务分析服务层（阶段 4 产品界面的后端核心）。

把「文件解析 → 入库 → 结构切块 → 构建 AgentGraph」封装成可复用服务，
FastAPI 层（main.py）与测试共用，避免业务逻辑散在接口里。

职责边界（方案 5.5/5.6 四概念分离）：
- Store（DuckDB）：权威业务数据 + AnalysisRun 运行记录；
- Checkpointer（SQLite）：会话状态快照（多轮续接）；
- 本层只做「编排」，不碰 LLM/工具内部逻辑（交给 AgentGraph）。
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agent import AgentGraph
from agent.checkpoint import Checkpointer
from agent.memory import SqliteMemoryStore
from agent.skills import build_skills, list_skill_meta
from auth import AccessPolicy, AuditLog, User
from domain import FinancialFact, StatementType
from finance.metrics import METRIC_DEFINITIONS, compute_metrics, compute_series
from finance.store import Store
from ingestion.excel_parser import parse_csv, parse_excel
from ingestion.pdf_parser import parse_pdf
from retrieval.chunker import chunk_pages

# PDF 三大报表都要入库（parse_pdf 每次只提取一个目标报表，故循环三次）
_STATEMENTS = (StatementType.BALANCE_SHEET, StatementType.INCOME_STATEMENT, StatementType.CASH_FLOW)

# 指标概览里做多期趋势的关键指标
_OVERVIEW_TREND_CODES = ["gross_margin", "net_margin", "roe", "revenue_growth", "net_profit_growth"]


def _serialize(obj: Any) -> Any:
    """把工具返回的嵌套数据转成 JSON 友好的 dict（Pydantic 模型递归 dump）。"""
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, list):
        return [_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def _metric_card(m) -> dict:
    """把 MetricResult 转成前端友好的卡片（ratio/growth 显示百分比）。"""
    kind = METRIC_DEFINITIONS[m.code].kind if m.code in METRIC_DEFINITIONS else ""
    card = {"code": m.code, "name": m.name, "group": m.group, "is_signal": m.is_signal}
    if m.computed:
        card["value"] = round(m.value, 6)
        card["display"] = f"{m.value:.2%}" if kind in ("ratio", "growth") else f"{m.value:,.2f}"
    else:
        card["value"] = None
        card["display"] = m.status.value
    return card


class FinanceService:
    """财务分析服务：管理 Store + chunks + AgentGraph 生命周期。

    db_path / checkpoint_path 传 ':memory:' 时进程内（测试用），
    传文件路径时持久化（界面用，落在 storage/ 下，git 忽略）。
    """

    def __init__(
        self,
        db_path: str = "storage/finance.duckdb",
        checkpoint_path: str = "storage/checkpoints.db",
        memory_path: str = "storage/memory.db",
        audit_path: str = "storage/audit.db",
    ):
        self.store = Store(db_path)
        self.checkpointer = Checkpointer(checkpoint_path)
        self.memory = SqliteMemoryStore(memory_path)
        self.audit_log = AuditLog(audit_path)
        self.access_policy = AccessPolicy()
        self._chunks: list = []
        self._graph: AgentGraph | None = None  # 懒构建，ingest 后失效

    # -- 入库 ---------------------------------------------------------------
    def ingest(
        self,
        path: str,
        company_name: str,
        fiscal_year: int,
        statement_type: str | None = None,
        is_excel: bool = False,
        user: User | None = None,
    ) -> dict:
        """入库一个财报文件（PDF 自动三大报表；CSV/Excel 按指定报表类型）。

        阶段 5：user 传入时先做写权限校验（越权 raise PermissionError 并记审计），
        成功后记录 ingest 审计事件。
        """
        if user is not None and not self.access_policy.can_write(user, company_name):
            self.audit_log.record(
                "access_denied", user_id=user.user_id, resource_type="company",
                resource_id=company_name, result="denied", detail=f"无权上传公司 {company_name}",
            )
            raise PermissionError(f"无权上传公司: {company_name}")

        result = self._ingest_impl(path, company_name, fiscal_year, statement_type, is_excel)
        self.audit_log.record(
            "ingest", user_id=user.user_id if user else None, resource_type="company",
            resource_id=company_name, result="success", detail=f"facts={result.get('facts')}",
        )
        return result

    def _ingest_impl(
        self,
        path: str,
        company_name: str,
        fiscal_year: int,
        statement_type: str | None = None,
        is_excel: bool = False,
    ) -> dict:
        path = str(path)
        lower = path.lower()
        if lower.endswith(".pdf"):
            return self._ingest_pdf(path, company_name, fiscal_year)

        st = self._statement_type(statement_type)
        if is_excel or lower.endswith((".xlsx", ".xls")):
            result = parse_excel(path, company_name, fiscal_year, st)
        else:
            result = parse_csv(path, company_name, fiscal_year, st)
        self._save_result(result)
        self._graph = None
        return {
            "report_id": result.report_doc.report_id,
            "company_name": company_name,
            "facts": len(result.facts),
        }

    def _ingest_pdf(self, path: str, company_name: str, fiscal_year: int) -> dict:
        """解析 PDF 三大报表 → 入库 facts/provenance → 结构切块一次。"""
        chunks: list = []
        total_facts = 0
        for st in _STATEMENTS:
            parsed = parse_pdf(path, company_name, fiscal_year, st)
            self._save_result(parsed)
            total_facts += len(parsed.facts)
            if not chunks:  # pages 三次相同，只切一次
                chunks = chunk_pages(
                    parsed.pages, company_name, fiscal_year, f"{company_name}-{fiscal_year}"
                )
        self._chunks.extend(chunks)
        self._graph = None
        return {
            "report_id": f"{company_name}-{fiscal_year}",
            "company_name": company_name,
            "facts": total_facts,
            "chunks": len(chunks),
        }

    def _save_result(self, result) -> None:
        """把解析产物（report_doc/facts/provenance）写入 Store。"""
        self.store.insert_report_doc(result.report_doc)
        self.store.insert_financial_facts(result.facts)
        for p in result.provenance:
            self.store.insert_provenance(p)

    @staticmethod
    def _statement_type(value: str | None) -> StatementType:
        if value in {s.value for s in StatementType}:
            return StatementType(value)
        return StatementType.BALANCE_SHEET

    # -- 问答 ---------------------------------------------------------------
    def ask(self, query: str, thread_id: str | None = None, user: User | None = None) -> dict:
        """对已入库财报提问，返回结构化结果（答案/意图/证据/冲突/缺失）。

        长期记忆接入：把「最近分析公司」作为 company_name 兜底传入 graph，
        ContextResolver 的「新指令优先」逻辑会让 query 里的公司覆盖记忆公司，
        用户不提公司名时则续接上次分析对象。

        阶段 5：user 传入时透传给 graph（工具层权限校验 + 审计）。
        """
        graph = self._get_graph()
        out = graph.run(
            query,
            company_name=self.memory.get("last_company"),
            known_companies=self.companies() or None,
            thread_id=thread_id,
            user=user,
        )
        if out.get("company_name"):
            self.memory.set("last_company", out["company_name"], kind="preference")
        return {
            "run_id": out.get("run_id"),
            "thread_id": out.get("thread_id"),
            "answer": out.get("final_answer"),
            "status": out.get("status"),
            "intent": out.get("intent"),
            "company_name": out.get("company_name"),
            "periods": out.get("periods", []),
            "missing_evidence": out.get("missing_evidence", []),
            "contradictions": out.get("contradictions", []),
            "tool_results": [
                {
                    "tool": r.tool,
                    "status": r.status.value,
                    "data": _serialize(r.data),
                    "period": r.period,
                    "scope": r.scope,
                    "unit": r.unit,
                    "sources": [s.model_dump() for s in r.sources],
                    "warnings": r.warnings,
                }
                for r in out.get("tool_results", [])
            ],
        }

    def _get_graph(self) -> AgentGraph:
        if self._graph is None:
            self._graph = AgentGraph(
                self.store,
                self._chunks,
                checkpointer=self.checkpointer,
                record_runs=True,
                access_policy=self.access_policy,
                audit_log=self.audit_log,
            )
        return self._graph

    # -- 查询 ---------------------------------------------------------------
    def companies(self) -> list[str]:
        """已入库的公司名（去重，供前端下拉与意图消歧）。"""
        rows = self.store.conn.execute(
            "SELECT DISTINCT company_name FROM financial_facts ORDER BY company_name"
        ).fetchall()
        return [r[0] for r in rows if r[0]]

    def runs(self, limit: int = 50) -> list[dict]:
        """历史运行记录（AnalysisRun，可回放审计）。"""
        return self.store.query_analysis_runs(limit=limit)

    def audit_events(self, user_id: str | None = None, action: str | None = None, limit: int = 100) -> list[dict]:
        """审计日志（阶段 5：谁在何时对什么资源做了什么）。"""
        return [e.model_dump(mode="json") for e in self.audit_log.query(user_id=user_id, action=action, limit=limit)]

    def get_run(self, run_id: str) -> dict | None:
        return self.store.get_analysis_run(run_id)

    # -- 长期记忆 -----------------------------------------------------------
    def memory_items(self) -> list[dict]:
        """列出全部长期记忆（偏好/可复用信息，跨会话持久化）。"""
        return [i.model_dump(mode="json") for i in self.memory.search()]

    def set_memory(
        self, key: str, value, kind: str = "reusable", ttl_seconds: int | None = None
    ) -> dict:
        return self.memory.set(key, value, kind=kind, ttl_seconds=ttl_seconds).model_dump(mode="json")

    def delete_memory(self, key: str) -> bool:
        return self.memory.delete(key)

    # -- 指标概览 / 报告导出 / 技能 -----------------------------------------
    def overview(self, company_name: str, fiscal_year: int) -> dict:
        """指标卡片（全 16 指标当前值）+ 关键指标多期趋势 + 关键科目。"""
        rows = self.store.query_facts(company_name=company_name, fiscal_year=fiscal_year)
        facts = [FinancialFact.model_validate(r) for r in rows]
        if not facts:
            return {"company": company_name, "year": fiscal_year, "metrics": [], "trends": {}, "facts": []}
        metrics = compute_metrics(facts, str(fiscal_year), "consolidated")
        metric_cards = [_metric_card(m) for m in metrics.values()]
        trends = {
            c: [_metric_card(m) for m in compute_series(c, facts, "consolidated")]
            for c in _OVERVIEW_TREND_CODES
        }
        key_items = {
            "revenue", "net_profit", "total_assets",
            "total_liabilities", "total_equity", "operating_cash_flow",
        }
        fact_cards = [
            {"item": f.standard_item, "value": f.value, "period": f.period}
            for f in facts if f.standard_item in key_items and f.value is not None
        ]
        return {
            "company": company_name, "year": fiscal_year,
            "metrics": metric_cards, "trends": trends, "facts": fact_cards,
        }

    def export_run(self, run_id: str) -> str | None:
        """把一次运行记录导出为 Markdown 报告（可回放审计的产物）。"""
        run = self.store.get_analysis_run(run_id)
        if not run:
            return None
        lines = [
            "# 财务分析报告", "",
            f"- 问题：{run.get('user_query')}",
            f"- 公司：{run.get('company_name') or '—'}",
            f"- 期间：{', '.join(run.get('periods', [])) or '—'}",
            f"- 意图：{run.get('intent') or '—'}",
            "", "## 结论", "",
            run.get("final_answer") or "（无）", "",
            "## 工具调用", "",
        ]
        for tc in run.get("tool_calls", []):
            src = ", ".join(tc.get("source_refs", []))
            line = f"- `{tc.get('tool')}`（{tc.get('status')}）：{tc.get('summary')}"
            lines.append(line + (f" [{src}]" if src else ""))
        if run.get("validation_summary"):
            lines += ["", "## 校验", ""]
            lines += [f"- {v}" for v in run["validation_summary"]]
        return "\n".join(lines)

    def list_skills(self) -> list[dict]:
        return list_skill_meta()

    def run_skill(self, code: str, company_name: str, fiscal_year: int | None = None) -> dict | None:
        """执行一个技能，返回结构化 SkillResult。"""
        skill = build_skills(self.store, self._chunks).get(code)
        if skill is None:
            return None
        return skill.run(company_name, fiscal_year).model_dump(mode="json")
