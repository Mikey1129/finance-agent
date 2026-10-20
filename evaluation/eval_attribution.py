"""eval_attribution —— 评测 C：证据归因（方案第 9 章）。

评测对象是 Agent 组装证据后的 EvidencePack（EvidenceAssembler 的确定性检查），
四个归因维度（方案第 9 章「C 证据归因」）：

1. **引用正确（Citation Accuracy）**：每条 source_ref 的 ref_id 都能回溯到真实输入
   （不悬空），且每个事实/指标/文本证据都有对应引用（不遗漏）。
2. **不把推测当事实（No speculation as fact）**：数值只来自数据层（FinancialFact）/
   计算层（MetricResult），冲突时不静默择一而是披露矛盾，缺失时显式提示而非猜测。
3. **不遗漏反向证据（No missing counter-evidence）**：同科目/期间/口径出现多个不同值、
   来源不一致（非当前公司/期间）必须披露。
4. **结论不超证据范围（Conclusion within evidence）**：按意图检查充分性，证据不足时
   sufficient=False 且 missing_evidence 明确列出缺什么，不硬答。

与 tests/test_evidence.py（单元级、测具体函数行为）不同，本模块是**场景化归因评测**：
每个场景给一个问题 + 一组输入证据 + 期望的归因断言，跑一遍 EvidenceAssembler 输出
通过/失败报告，作为阶段 3/4 评测 C 的确定性锚点。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.evidence import EvidenceAssembler, EvidencePack
from agent.state import Intent
from domain import EvidenceChunk, FinancialFact, StatementType
from finance.metrics import MetricResult, MetricStatus


# -- 评测场景 ---------------------------------------------------------------
@dataclass
class AttributionCase:
    """一个证据归因评测场景。"""

    name: str
    query: str
    intent: Intent | None = None
    company_name: str | None = None
    periods: list[str] = field(default_factory=list)
    facts: list[FinancialFact] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    chunks: list[EvidenceChunk] = field(default_factory=list)
    expect_sufficient: bool = True
    expect_contradiction: bool = False
    expect_missing_keywords: tuple[str, ...] = ()


@dataclass
class CaseResult:
    name: str
    passed: bool
    failures: list[str] = field(default_factory=list)


@dataclass
class AttributionEvalResult:
    company_name: str | None
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def failed_count(self) -> int:
        return len(self.cases) - self.passed_count

    @property
    def passed(self) -> bool:
        return self.failed_count == 0 and bool(self.cases)


# -- 归因检查 ---------------------------------------------------------------
def _check_refs_traceable(
    pack: EvidencePack, facts: list[FinancialFact], chunks: list[EvidenceChunk], metrics: dict
) -> list[str]:
    """引用可回溯：每条 source_ref 的 ref_id 都能在输入里找到（不悬空）。"""
    fact_ids = {f.fact_id for f in facts}
    chunk_ids = {c.chunk_id for c in chunks}
    metric_codes = set(metrics.keys())
    dangling: list[str] = []
    for ref in pack.source_refs:
        if ref.kind == "fact" and ref.ref_id not in fact_ids:
            dangling.append(f"fact:{ref.ref_id}")
        elif ref.kind == "chunk" and ref.ref_id not in chunk_ids:
            dangling.append(f"chunk:{ref.ref_id}")
        elif ref.kind == "metric" and ref.ref_id not in metric_codes:
            dangling.append(f"metric:{ref.ref_id}")
    return dangling


def _check_refs_complete(
    pack: EvidencePack, facts: list[FinancialFact], chunks: list[EvidenceChunk], metrics: dict
) -> list[str]:
    """引用完整：每个事实/指标/文本证据都有对应 source_ref（不遗漏）。"""
    ref_ids = {(r.kind, r.ref_id) for r in pack.source_refs}
    missing: list[str] = []
    for f in facts:
        if ("fact", f.fact_id) not in ref_ids:
            missing.append(f"fact:{f.fact_id}")
    for c in chunks:
        if ("chunk", c.chunk_id) not in ref_ids:
            missing.append(f"chunk:{c.chunk_id}")
    for code in metrics:
        if ("metric", code) not in ref_ids:
            missing.append(f"metric:{code}")
    return missing


# -- 构造助手 ---------------------------------------------------------------
def _fact(fid, item, value, period="2024", scope="consolidated", company="公牛集团", year=2024):
    return FinancialFact(
        fact_id=fid, report_id="r1", company_name=company, fiscal_year=year,
        raw_item=item, standard_item=item, value=value,
        statement_type=StatementType.INCOME_STATEMENT, period=period,
        consolidation_scope=scope,
    )


def _chunk(cid, company="公牛集团", year=2024, text="应收规模扩大，主要因销售回款放缓"):
    return EvidenceChunk(chunk_id=cid, report_id="r1", company_name=company, fiscal_year=year, text=text)


def _metric(value=0.4, status=MetricStatus.COMPUTED):
    return MetricResult(
        code="gross_margin", name="毛利率", group="盈利能力", period="2024",
        scope="consolidated", formula="毛利/营收", value=value, status=status,
    )


# -- 内置场景（覆盖四维度 + 边界） ------------------------------------------
def build_cases() -> list[AttributionCase]:
    cases: list[AttributionCase] = []

    # 维度 1 引用正确 + 维度 4 正面：三类证据齐全，引用可回溯且完整，证据充分
    cases.append(
        AttributionCase(
            name="refs_traceable_and_complete",
            query="2024 毛利率如何？",
            intent=Intent.METRIC_ANALYSIS,
            company_name="公牛集团",
            periods=["2024"],
            facts=[_fact("f1", "revenue", 2000.0)],
            metrics={"gross_margin": _metric()},
            chunks=[_chunk("c1")],
            expect_sufficient=True,
        )
    )

    # 维度 3 反向证据（冲突披露）：同科目同期间同口径两个不同值 → 披露矛盾
    cases.append(
        AttributionCase(
            name="contradiction_same_item_disclosed",
            query="营收是多少",
            intent=Intent.FACT_QUERY,
            facts=[_fact("f1", "revenue", 100.0), _fact("f2", "revenue", 200.0)],
            expect_contradiction=True,
        )
    )

    # 维度 3 反向证据（来源不一致）：chunk 属其他公司 → 披露来源不一致；
    # 同时归因缺有值事实 → 不充分（一并验证）
    cases.append(
        AttributionCase(
            name="source_mismatch_disclosed",
            query="为什么现金流下滑",
            intent=Intent.ATTRIBUTION,
            company_name="公牛集团",
            periods=["2024"],
            chunks=[_chunk("c1", company="其他公司")],
            expect_contradiction=True,
            expect_sufficient=False,
        )
    )

    # 边界：同科目不同 scope 不是冲突（scope 是口径，不算反向证据）
    cases.append(
        AttributionCase(
            name="no_false_conflict_across_scope",
            query="营收",
            facts=[_fact("f1", "revenue", 100.0, scope="consolidated"),
                   _fact("f2", "revenue", 90.0, scope="parent_only")],
            expect_contradiction=False,
        )
    )

    # 维度 4 不超证据范围：归因缺文本证据 → 不充分且明确提示
    cases.append(
        AttributionCase(
            name="attribution_missing_text",
            query="为什么净利率下降",
            intent=Intent.ATTRIBUTION,
            facts=[_fact("f1", "revenue", 100.0)],
            chunks=[],
            expect_sufficient=False,
            expect_missing_keywords=("文本证据",),
        )
    )

    # 维度 4：精确数值查询缺事实 → 不充分
    cases.append(
        AttributionCase(
            name="fact_query_missing_fact",
            query="营收多少",
            intent=Intent.FACT_QUERY,
            expect_sufficient=False,
            expect_missing_keywords=("财务事实",),
        )
    )

    # 维度 4：指标全 insufficient → 不充分
    cases.append(
        AttributionCase(
            name="metric_all_insufficient",
            query="净利率",
            intent=Intent.METRIC_ANALYSIS,
            metrics={"net_margin": _metric(status=MetricStatus.INSUFFICIENT_DATA)},
            expect_sufficient=False,
        )
    )

    # 维度 2 不把推测当事实：value=None 的事实不算有值，不参与冲突也不构成证据
    cases.append(
        AttributionCase(
            name="none_value_fact_ignored",
            query="营收",
            intent=Intent.FACT_QUERY,
            facts=[_fact("f1", "revenue", None), _fact("f2", "revenue", None)],
            expect_contradiction=False,
            expect_sufficient=False,
            expect_missing_keywords=("财务事实",),
        )
    )

    # 边界：空输入不崩溃（无 intent 无要求 → 无缺失）
    cases.append(
        AttributionCase(
            name="empty_input_no_crash",
            query="随便问一句",
            intent=None,
            expect_sufficient=True,
            expect_contradiction=False,
        )
    )

    return cases


# -- 评测入口 ---------------------------------------------------------------
def run_case(case: AttributionCase) -> CaseResult:
    pack = EvidenceAssembler().assemble(
        case.query,
        intent=case.intent,
        company_name=case.company_name,
        periods=case.periods,
        facts=case.facts,
        metrics=case.metrics,
        chunks=case.chunks,
    )
    failures: list[str] = []

    if pack.sufficient != case.expect_sufficient:
        failures.append(
            f"充分性不符：期望 sufficient={case.expect_sufficient}，实际={pack.sufficient}"
        )
    if pack.has_contradictions != case.expect_contradiction:
        failures.append(
            f"冲突披露不符：期望={case.expect_contradiction}，实际={pack.has_contradictions}"
        )
    for kw in case.expect_missing_keywords:
        if not any(kw in m for m in pack.missing_evidence):
            failures.append(f"缺失证据未包含关键词「{kw}」：实际={pack.missing_evidence}")

    dangling = _check_refs_traceable(pack, case.facts, case.chunks, case.metrics)
    if dangling:
        failures.append(f"悬空引用：{dangling}")
    incomplete = _check_refs_complete(pack, case.facts, case.chunks, case.metrics)
    if incomplete:
        failures.append(f"遗漏引用：{incomplete}")

    return CaseResult(name=case.name, passed=not failures, failures=failures)


def eval_attribution(cases: list[AttributionCase] | None = None) -> AttributionEvalResult:
    """跑全部归因评测场景，返回通过/失败报告。"""
    cases = cases or build_cases()
    results = [run_case(c) for c in cases]
    # 归因评测不绑定单一公司，company_name 留 None 表示通用逻辑评测
    return AttributionEvalResult(company_name=None, cases=results)
