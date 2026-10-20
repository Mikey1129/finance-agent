"""agent.evidence —— 证据组装与充分性检查（方案 5.11 / 5.9）。

EvidenceAssembler 把 SQL 结果（financial_facts）、计算结果（computed_metrics）、
RAG 片段（text_evidence）统一整理成 EvidencePack，并做三类检查：
  1. 来源一致性——文本证据是否属当前公司/报告/期间；
  2. 冲突披露——同一科目/期间/口径出现多个不同值；
  3. 充分性——按意图判断是否缺少必要证据（方案 5.9 第 1/4 条）。

「检索到若干文本」≠「已获得足以支持结论的证据」（方案 5.11）——证据不足时应
继续检索、请求澄清或明确说明，而不是扩大检索范围直到找到看似合理的答案。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent.state import Intent
from agent.tools import SourceRef
from domain import EvidenceChunk, FinancialFact


class EvidencePack(BaseModel):
    """结构化证据包（方案 5.11）。"""

    query: str = Field(description="分析问题")
    company_name: str | None = Field(default=None, description="分析对象公司")
    periods: list[str] = Field(default_factory=list, description="分析期间")

    financial_facts: list[FinancialFact] = Field(default_factory=list, description="SQL 返回的财务事实")
    computed_metrics: dict[str, Any] = Field(
        default_factory=dict, description="指标 code -> MetricResult（dataclass，非 Pydantic 模型）"
    )
    text_evidence: list[EvidenceChunk] = Field(default_factory=list, description="RAG 检索的文本片段")

    source_refs: list[SourceRef] = Field(default_factory=list, description="来源引用")
    contradictions: list[str] = Field(default_factory=list, description="冲突披露/来源不一致")
    missing_evidence: list[str] = Field(default_factory=list, description="缺失的必要证据")

    @property
    def sufficient(self) -> bool:
        return not self.missing_evidence

    @property
    def has_contradictions(self) -> bool:
        return bool(self.contradictions)

    @property
    def computed_count(self) -> int:
        return sum(1 for m in self.computed_metrics.values() if getattr(m, "computed", False))


def _find_contradictions(facts: list[FinancialFact]) -> list[str]:
    """同一 (科目, 期间, 口径) 出现多个不同数值 → 冲突披露。"""
    by_key: dict[tuple[str, str, str], set[float]] = {}
    for f in facts:
        if not f.has_value() or f.period is None:
            continue
        key = (f.standard_item, f.period, f.consolidation_scope)
        by_key.setdefault(key, set()).add(f.value)

    conflicts: list[str] = []
    for (item, period, scope), vals in by_key.items():
        if len(vals) > 1:
            conflicts.append(
                f"{item}@{period}({scope}) 出现 {len(vals)} 个不同值: {sorted(vals)}"
            )
    return conflicts


def _find_source_mismatch(
    chunks: list[EvidenceChunk], company_name: str | None, periods: list[str]
) -> list[str]:
    """文本证据来源一致性：是否属当前公司/期间。"""
    mismatches: list[str] = []
    for c in chunks:
        if company_name and c.company_name != company_name:
            mismatches.append(f"chunk {c.chunk_id} 属于 {c.company_name}，非当前公司 {company_name}")
        if periods and c.fiscal_year is not None and str(c.fiscal_year) not in periods:
            mismatches.append(f"chunk {c.chunk_id} 年度 {c.fiscal_year} 不在分析期间 {periods}")
    return mismatches


def _find_missing(
    facts: list[FinancialFact],
    metrics: dict[str, Any],
    chunks: list[EvidenceChunk],
    intent: Intent | None,
) -> list[str]:
    """按意图判断缺失的必要证据（方案 5.3 三条路径）。"""
    missing: list[str] = []
    if intent is Intent.FACT_QUERY and not facts:
        missing.append("缺少财务事实数据")
    if intent is Intent.METRIC_ANALYSIS:
        if not metrics:
            missing.append("缺少指标计算结果")
        elif metrics and all(not getattr(m, "computed", False) for m in metrics.values()):
            missing.append("全部指标 insufficient_data（缺科目或缺上期）")
    if intent is Intent.ATTRIBUTION:
        if not facts:
            missing.append("缺少财务事实数据")
        if not chunks:
            missing.append("缺少文本证据（MD&A/附注）")
    return missing


class EvidenceAssembler:
    """把 SQL 结果 / 计算结果 / RAG 片段统一组装为 EvidencePack 并做检查。"""

    def assemble(
        self,
        query: str,
        intent: Intent | None = None,
        company_name: str | None = None,
        periods: list[str] | None = None,
        facts: list[FinancialFact] | None = None,
        metrics: dict[str, Any] | None = None,
        chunks: list[EvidenceChunk] | None = None,
    ) -> EvidencePack:
        facts = facts or []
        metrics = metrics or {}
        chunks = chunks or []
        periods = periods or []

        source_refs: list[SourceRef] = []
        source_refs.extend(SourceRef(kind="fact", ref_id=f.fact_id, report_id=f.report_id) for f in facts)
        source_refs.extend(SourceRef(kind="metric", ref_id=c) for c in metrics)
        source_refs.extend(
            SourceRef(kind="chunk", ref_id=c.chunk_id, report_id=c.report_id, page=c.page_start)
            for c in chunks
        )

        contradictions = _find_contradictions(facts) + _find_source_mismatch(chunks, company_name, periods)
        missing = _find_missing(facts, metrics, chunks, intent)

        return EvidencePack(
            query=query,
            company_name=company_name,
            periods=periods,
            financial_facts=facts,
            computed_metrics=metrics,
            text_evidence=chunks,
            source_refs=source_refs,
            contradictions=contradictions,
            missing_evidence=missing,
        )
