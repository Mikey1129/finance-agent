"""agent.tools —— 工具注册与 Pydantic Schema。

对应方案 5.4「Function Calling 与工具执行」+ 第 6 章 ToolResult。
第一版 5 个核心工具：query_financial_fact / compute_metric / compare_periods /
retrieve_evidence / validate_result。

设计要点（方案 5.4）：
- 工具统一注册，输入/输出用 Pydantic Schema；
- 工具独立执行，返回结构化 ToolResult（状态/数据/单位/口径/来源/错误）；
- 工具失败返回明确错误类型（ToolError.type），由工作流决定重试/降级/终止；
- 不允许模型生成的任意 SQL/Python 未经限制执行——所有查询走 Store 的白名单参数化接口。

retrieve_evidence 的后端是 retrieval/retriever.py 的 BM25 基线（含元数据过滤），
BGE 语义召回与 Reranker 阶段 3/4 接入。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from domain import EvidenceChunk, FinancialFact, StatementType
from finance.metrics import METRIC_DEFINITIONS, MetricResult, compute_metrics, compute_series
from finance.store import Store
from finance.taxonomy import STANDARD_ITEMS, map_item
from finance.validation import ValidationReport, check_balance_sheet, validate_fact
from retrieval.retriever import Retriever
from retrieval.reranker import build_reranker


class ToolStatus(str, Enum):
    """工具执行状态。SUCCESS=成功返回；ERROR=工具级失败；NO_RESULT=成功但无匹配。"""

    SUCCESS = "success"
    ERROR = "error"
    NO_RESULT = "no_result"


class ToolError(BaseModel):
    """工具错误：类型 + 消息。工作流据此决定重试/降级/终止（方案 5.4）。"""

    type: str = Field(description="错误类型，如 invalid_param / unknown_metric / insufficient_data")
    message: str


class SourceRef(BaseModel):
    """来源引用（方案 6 章 EvidenceRef 的轻量版，指向 fact / metric / chunk）。"""

    kind: str = Field(description="fact | metric | chunk")
    ref_id: str = Field(description="fact_id / metric code / chunk_id")
    report_id: str | None = None
    page: int | None = None
    table_name: str | None = None
    cell: str | None = None


class ToolResult(BaseModel):
    """工具统一返回结构：状态 / 数据 / 单位 / 口径 / 来源 / 错误（方案 6 章）。"""

    tool: str
    status: ToolStatus
    data: Any | None = None
    period: str | None = Field(default=None, description="期间标识")
    scope: str | None = Field(default=None, description="合并范围 consolidated/parent_only")
    unit: str | None = Field(default=None, description="数值单位，内部统一为「元」")
    sources: list[SourceRef] = Field(default_factory=list)
    error: ToolError | None = None
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def ok(cls, tool: str, data: Any = None, **meta: Any) -> "ToolResult":
        return cls(tool=tool, status=ToolStatus.SUCCESS, data=data, **meta)

    @classmethod
    def fail(cls, tool: str, err_type: str, message: str, **meta: Any) -> "ToolResult":
        return cls(
            tool=tool,
            status=ToolStatus.ERROR,
            error=ToolError(type=err_type, message=message),
            **meta,
        )


class BaseTool(ABC):
    """工具基类：绑定 Store，输入 Schema 由子类定义，run 返回 ToolResult。"""

    name: str = ""
    description: str = ""
    input_schema: type[BaseModel]

    def __init__(self, store: Store):
        self.store = store

    @abstractmethod
    def run(self, args: BaseModel) -> ToolResult:
        """执行工具，返回结构化结果。"""

    def __call__(self, **kwargs: Any) -> ToolResult:
        return self.run(self.input_schema(**kwargs))


# ---------------------------------------------------------------------------
# 工具内部辅助
# ---------------------------------------------------------------------------
def _facts_from_store(
    store: Store, company_name: str, fiscal_year: int | None = None
) -> list[FinancialFact]:
    """从 Store 查财务事实并还原为 FinancialFact 对象。"""
    rows = store.query_facts(company_name=company_name, fiscal_year=fiscal_year)
    return [FinancialFact.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# 1. query_financial_fact —— 查询结构化财务事实
# ---------------------------------------------------------------------------
class QueryFinancialFactInput(BaseModel):
    company_name: str = Field(description="公司名称")
    fiscal_year: int | None = Field(default=None, description="财务年度，如 2025")
    standard_item: str | None = Field(default=None, description="标准科目标识符，如 revenue")
    statement_type: StatementType | None = Field(default=None, description="报表类型")
    scope: str | None = Field(default=None, description="合并范围 consolidated / parent_only")


class QueryFinancialFactTool(BaseTool):
    name = "query_financial_fact"
    description = "查询结构化财务事实，支持按公司/年度/科目/报表/合并范围过滤。"
    input_schema = QueryFinancialFactInput

    def run(self, args: QueryFinancialFactInput) -> ToolResult:
        if not args.company_name.strip():
            return ToolResult.fail(self.name, "invalid_param", "company_name 不能为空")
        # LLM 可能传中文科目名（如「营业收入」），用 taxonomy 映射到标准标识符兜底
        std_item = args.standard_item
        if std_item and std_item not in STANDARD_ITEMS:
            std_item = map_item(std_item)
        rows = self.store.query_facts(
            company_name=args.company_name,
            fiscal_year=args.fiscal_year,
            standard_item=std_item,
            statement_type=args.statement_type.value if args.statement_type else None,
        )
        facts = [FinancialFact.model_validate(r) for r in rows]
        if args.scope:
            facts = [f for f in facts if f.consolidation_scope == args.scope]
        if not facts:
            return ToolResult(
                tool=self.name, status=ToolStatus.NO_RESULT, data=[], scope=args.scope,
                warnings=["无匹配的财务事实"],
            )
        return ToolResult.ok(
            self.name,
            data=facts,
            scope=args.scope,
            unit="元",
            sources=[SourceRef(kind="fact", ref_id=f.fact_id, report_id=f.report_id) for f in facts],
        )


# ---------------------------------------------------------------------------
# 2. compute_metric —— 计算已注册的财务指标
# ---------------------------------------------------------------------------
class ComputeMetricInput(BaseModel):
    company_name: str = Field(description="公司名称")
    fiscal_year: int = Field(description="财务年度，如 2025")
    metric_codes: list[str] = Field(default_factory=list, description="指标 code，空=全部 16 个")
    scope: str = "consolidated"
    period: str | None = Field(default=None, description="期间标识，默认取 fiscal_year")


class ComputeMetricTool(BaseTool):
    name = "compute_metric"
    description = "计算已注册的财务指标（16 个），分母为 0 或缺失时不返回假数。"
    input_schema = ComputeMetricInput

    def run(self, args: ComputeMetricInput) -> ToolResult:
        facts = _facts_from_store(self.store, args.company_name, args.fiscal_year)
        if not facts:
            return ToolResult.fail(
                self.name, "insufficient_data", f"无 {args.company_name} {args.fiscal_year} 的数据"
            )
        unknown = [c for c in args.metric_codes if c not in METRIC_DEFINITIONS]
        if unknown:
            return ToolResult.fail(self.name, "unknown_metric", f"未注册的指标: {unknown}")
        period = args.period or str(args.fiscal_year)
        metrics = compute_metrics(facts, period, args.scope, args.metric_codes or None)
        warnings: list[str] = []
        if metrics and all(not m.computed for m in metrics.values()):
            warnings.append("所有指标均 insufficient_data（可能缺科目或缺上期）")
        return ToolResult.ok(
            self.name,
            data=metrics,
            period=period,
            scope=args.scope,
            warnings=warnings,
            sources=[SourceRef(kind="metric", ref_id=c) for c in metrics],
        )


# ---------------------------------------------------------------------------
# 3. compare_periods —— 比较多个报告期间
# ---------------------------------------------------------------------------
class ComparePeriodsInput(BaseModel):
    company_name: str = Field(description="公司名称")
    metric_codes: list[str] = Field(description="指标 code 列表")
    scope: str = "consolidated"
    fiscal_year: int | None = Field(default=None, description="可选，只看该年及之前")


class ComparePeriodsTool(BaseTool):
    name = "compare_periods"
    description = "跨期间计算同一组指标，输出按时间顺序的序列（多期比较）。"
    input_schema = ComparePeriodsInput

    def run(self, args: ComparePeriodsInput) -> ToolResult:
        facts = _facts_from_store(self.store, args.company_name, args.fiscal_year)
        if not facts:
            return ToolResult.fail(
                self.name, "insufficient_data", f"无 {args.company_name} 的数据"
            )
        unknown = [c for c in args.metric_codes if c not in METRIC_DEFINITIONS]
        if unknown:
            return ToolResult.fail(self.name, "unknown_metric", f"未注册的指标: {unknown}")
        series = {c: compute_series(c, facts, args.scope) for c in args.metric_codes}
        periods = sorted({f.period for f in facts if f.period and f.consolidation_scope == args.scope})
        return ToolResult.ok(
            self.name,
            data=series,
            period=",".join(periods),
            scope=args.scope,
            sources=[SourceRef(kind="metric", ref_id=c) for c in args.metric_codes],
        )


# ---------------------------------------------------------------------------
# 3.5 compare_companies —— 跨公司横向比较（阶段 5）
# ---------------------------------------------------------------------------
class CompareCompaniesInput(BaseModel):
    company_names: list[str] = Field(description="要对比的公司名列表（至少 2 个，去重）")
    fiscal_year: int = Field(description="财务年度，如 2025")
    metric_codes: list[str] = Field(default_factory=list, description="指标 code，空=全部 16 个")
    scope: str = "consolidated"


class CompareCompaniesTool(BaseTool):
    name = "compare_companies"
    description = "跨公司横向比较同一组财务指标，输出各公司指标值对比（阶段 5 多公司比较）。"
    input_schema = CompareCompaniesInput

    def run(self, args: CompareCompaniesInput) -> ToolResult:
        names = list(dict.fromkeys(args.company_names))  # 去重保序
        if len(names) < 2:
            return ToolResult.fail(self.name, "invalid_param", "至少需要 2 家公司对比")
        unknown = [c for c in args.metric_codes if c not in METRIC_DEFINITIONS]
        if unknown:
            return ToolResult.fail(self.name, "unknown_metric", f"未注册的指标: {unknown}")

        period = str(args.fiscal_year)
        comparison: dict[str, Any] = {}
        no_data: list[str] = []
        for company in names:
            facts = _facts_from_store(self.store, company, args.fiscal_year)
            if not facts:
                no_data.append(company)
                comparison[company] = None
                continue
            comparison[company] = compute_metrics(facts, period, args.scope, args.metric_codes or None)

        warnings: list[str] = []
        if no_data:
            warnings.append(f"无数据的公司: {', '.join(no_data)}")
        return ToolResult.ok(
            self.name,
            data=comparison,
            period=period,
            scope=args.scope,
            warnings=warnings,
            sources=[SourceRef(kind="company", ref_id=c) for c in names],
        )


# ---------------------------------------------------------------------------
# 4. retrieve_evidence —— 检索年报 / MD&A / 财务附注
# ---------------------------------------------------------------------------
class RetrieveEvidenceInput(BaseModel):
    query: str = Field(description="检索问题或关键词")
    company_name: str | None = Field(default=None, description="按公司过滤")
    fiscal_year: int | None = Field(default=None, description="按年度过滤")
    section: str | None = Field(default=None, description="按章节路径关键词过滤")
    top_k: int = Field(default=5, description="返回片段数", ge=1, le=20)


class RetrieveEvidenceTool(BaseTool):
    name = "retrieve_evidence"
    description = "检索年报、MD&A 与财务附注文本证据，返回带来源（章节/页码）的片段。"
    input_schema = RetrieveEvidenceInput

    def __init__(self, store: Store, chunks: list[EvidenceChunk] | None = None, reranker=None):
        super().__init__(store)
        self.chunks = chunks or []
        # 显式开关（ENABLE_RERANKER=1）才启用重排；默认纯 BM25
        if reranker is None:
            reranker = build_reranker()
        self._retriever = Retriever(self.chunks, reranker=reranker)

    def run(self, args: RetrieveEvidenceInput) -> ToolResult:
        if not args.query.strip():
            return ToolResult.fail(self.name, "invalid_param", "query 不能为空")
        if not self.chunks:
            return ToolResult.fail(self.name, "no_evidence_source", "无可检索的文本证据源")

        top = self._retriever.retrieve(
            args.query,
            company_name=args.company_name,
            fiscal_year=args.fiscal_year,
            section=args.section,
            top_k=args.top_k,
        )

        if not top:
            return ToolResult(
                tool=self.name, status=ToolStatus.NO_RESULT, data=[],
                warnings=["无匹配文本片段"],
            )
        return ToolResult.ok(
            self.name,
            data=top,
            sources=[
                SourceRef(
                    kind="chunk", ref_id=c.chunk_id, report_id=c.report_id,
                    page=c.page_start,
                )
                for c in top
            ],
        )


# ---------------------------------------------------------------------------
# 5. validate_result —— 执行数据、口径及来源校验
# ---------------------------------------------------------------------------
class ValidateResultInput(BaseModel):
    company_name: str | None = Field(default=None, description="按公司过滤")
    fiscal_year: int | None = Field(default=None, description="按年度过滤")
    check: str = Field(default="all", description="fact | balance | all")


class ValidateResultTool(BaseTool):
    name = "validate_result"
    description = "执行数据校验（事实状态/单位/科目映射 + 资产负债表平衡）。"
    input_schema = ValidateResultInput

    def run(self, args: ValidateResultInput) -> ToolResult:
        facts = _facts_from_store(self.store, args.company_name, args.fiscal_year)
        if not facts:
            return ToolResult.fail(self.name, "insufficient_data", "无可校验的数据")
        if args.check not in ("fact", "balance", "all"):
            return ToolResult.fail(
                self.name, "invalid_param", f"check 只支持 fact/balance/all，收到 {args.check}"
            )

        report = ValidationReport()
        if args.check in ("fact", "all"):
            for f in facts:
                for issue in validate_fact(f):
                    report.add(issue)
        if args.check in ("balance", "all"):
            for issue in check_balance_sheet(facts).issues:
                report.add(issue)

        warnings = []
        if report.errors:
            warnings.append(f"发现 {report.errors} 个错误")
        return ToolResult.ok(
            self.name,
            data=report,
            scope=",".join(sorted({f.consolidation_scope for f in facts})),
            warnings=warnings,
        )


# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------
class ToolRegistry:
    """按名称注册工具，提供 schemas()（给 LLM Function Calling）与 invoke()。"""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def schemas(self) -> list[dict[str, Any]]:
        """导出 OpenAI Function Calling 风格的 JSON Schema 列表。"""
        out = []
        for tool in self._tools.values():
            out.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema.model_json_schema(),
                }
            )
        return out

    def invoke(self, name: str, **kwargs: Any) -> ToolResult:
        """按名称调用工具；未知工具或参数非法统一转为 ERROR。"""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.fail(name, "unknown_tool", f"未注册的工具: {name}")
        try:
            args = tool.input_schema(**kwargs)
        except Exception as e:  # pydantic.ValidationError 等
            return ToolResult.fail(name, "invalid_param", f"参数非法: {e}")
        return tool.run(args)


def build_tools(store: Store, chunks: list[EvidenceChunk] | None = None) -> ToolRegistry:
    """构造注册了 6 个核心工具的 ToolRegistry（含阶段 5 compare_companies）。"""
    registry = ToolRegistry()
    registry.register(QueryFinancialFactTool(store))
    registry.register(ComputeMetricTool(store))
    registry.register(ComparePeriodsTool(store))
    registry.register(CompareCompaniesTool(store))
    registry.register(RetrieveEvidenceTool(store, chunks))
    registry.register(ValidateResultTool(store))
    return registry
