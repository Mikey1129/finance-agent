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
from domain import StatementType
from finance.store import Store
from ingestion.excel_parser import parse_csv, parse_excel
from ingestion.pdf_parser import parse_pdf
from retrieval.chunker import chunk_pages

# PDF 三大报表都要入库（parse_pdf 每次只提取一个目标报表，故循环三次）
_STATEMENTS = (StatementType.BALANCE_SHEET, StatementType.INCOME_STATEMENT, StatementType.CASH_FLOW)


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


class FinanceService:
    """财务分析服务：管理 Store + chunks + AgentGraph 生命周期。

    db_path / checkpoint_path 传 ':memory:' 时进程内（测试用），
    传文件路径时持久化（界面用，落在 storage/ 下，git 忽略）。
    """

    def __init__(
        self,
        db_path: str = "storage/finance.duckdb",
        checkpoint_path: str = "storage/checkpoints.db",
    ):
        self.store = Store(db_path)
        self.checkpointer = Checkpointer(checkpoint_path)
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
    ) -> dict:
        """入库一个财报文件（PDF 自动三大报表；CSV/Excel 按指定报表类型）。"""
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
    def ask(self, query: str, thread_id: str | None = None) -> dict:
        """对已入库财报提问，返回结构化结果（答案/意图/证据/冲突/缺失）。"""
        graph = self._get_graph()
        out = graph.run(
            query,
            known_companies=self.companies() or None,
            thread_id=thread_id,
        )
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

    def get_run(self, run_id: str) -> dict | None:
        return self.store.get_analysis_run(run_id)
