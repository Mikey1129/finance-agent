"""agent.evidence 证据组装与充分性检查测试。"""

from agent.evidence import EvidenceAssembler
from agent.state import Intent
from domain import EvidenceChunk, FinancialFact, StatementType
from finance.metrics import MetricResult, MetricStatus


def _fact(fid, item, value, period="2024", scope="consolidated"):
    return FinancialFact(
        fact_id=fid, report_id="r1", company_name="c", fiscal_year=2024,
        raw_item=item, standard_item=item, value=value,
        statement_type=StatementType.INCOME_STATEMENT, period=period,
        consolidation_scope=scope,
    )


def _chunk(cid, company="c", year=2024):
    return EvidenceChunk(chunk_id=cid, report_id="r1", company_name=company, fiscal_year=year, text="x")


def _metric(value=0.4, status=MetricStatus.COMPUTED):
    return MetricResult(
        code="gross_margin", name="毛利率", group="盈利能力", period="2024",
        scope="consolidated", formula="x", value=value, status=status,
    )


def test_assemble_builds_source_refs():
    pack = EvidenceAssembler().assemble(
        "2024 毛利率？", intent=Intent.METRIC_ANALYSIS, company_name="c", periods=["2024"],
        facts=[_fact("f1", "revenue", 2000.0)],
        metrics={"gross_margin": _metric()},
        chunks=[_chunk("c1")],
    )
    assert {r.kind for r in pack.source_refs} == {"fact", "metric", "chunk"}
    assert pack.sufficient
    assert pack.computed_count == 1


def test_contradiction_same_item_two_values():
    facts = [_fact("f1", "revenue", 100.0), _fact("f2", "revenue", 200.0)]
    pack = EvidenceAssembler().assemble("营收", facts=facts)
    assert pack.has_contradictions
    assert any("revenue" in c for c in pack.contradictions)


def test_source_mismatch_company():
    pack = EvidenceAssembler().assemble("x", company_name="c", chunks=[_chunk("c1", company="其他公司")])
    assert any("其他公司" in c for c in pack.contradictions)


def test_missing_fact_query():
    pack = EvidenceAssembler().assemble("营收多少", intent=Intent.FACT_QUERY)
    assert not pack.sufficient
    assert any("财务事实" in m for m in pack.missing_evidence)


def test_missing_metric_all_insufficient():
    pack = EvidenceAssembler().assemble(
        "净利率", intent=Intent.METRIC_ANALYSIS,
        metrics={"net_margin": _metric(status=MetricStatus.INSUFFICIENT_DATA)},
    )
    assert not pack.sufficient


def test_missing_attribution_needs_text():
    pack = EvidenceAssembler().assemble(
        "为什么下降", intent=Intent.ATTRIBUTION,
        facts=[_fact("f1", "revenue", 100.0)], chunks=[],
    )
    assert not pack.sufficient
    assert any("文本证据" in m for m in pack.missing_evidence)
