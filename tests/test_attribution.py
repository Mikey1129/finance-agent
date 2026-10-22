"""agent.attribution 结论合成测试：证据上下文组装 + JSON 容错 + 报告渲染 + graph 集成。"""

import json

from agent.attribution import (
    AttributionConclusion,
    AttributionDraft,
    AttributionReason,
    AttributionWriter,
    EvidenceLevel,
    build_attribution_writer,
    build_evidence_context,
    render_report,
)
from agent.context import ContextPolicy
from agent.evidence import EvidencePack
from agent.graph import AgentGraph
from agent.llm import LLMPlanner
from agent.tools import SourceRef
from domain import EvidenceChunk, FinancialFact, StatementType
from finance.store import Store


# -- 测试数据 ---------------------------------------------------------------
def _pack():
    facts = [
        FinancialFact(
            fact_id="ocf-2024", report_id="r1", company_name="测试公司",
            fiscal_year=2024, raw_item="operating_cash_flow", standard_item="operating_cash_flow",
            value=180.0, unit="元", statement_type=StatementType.CASH_FLOW,
            period="2024", consolidation_scope="consolidated",
        ),
        # 无值事实：build_evidence_context 应过滤掉
        FinancialFact(
            fact_id="no-val", report_id="r1", company_name="测试公司",
            fiscal_year=2024, raw_item="x", standard_item="x", value=None,
            unit="元", statement_type=StatementType.CASH_FLOW,
            period="2024", consolidation_scope="consolidated",
        ),
    ]
    chunks = [
        EvidenceChunk(
            chunk_id="c1", report_id="r1", company_name="测试公司", fiscal_year=2024,
            section_path="现金流量表附注", page_start=45, text="预收货款下降所致",
        ),
    ]
    return EvidencePack(
        query="为什么经营现金流下降",
        company_name="测试公司",
        periods=["2024"],
        financial_facts=facts,
        computed_metrics={},
        text_evidence=chunks,
    )


_GOOD_JSON = json.dumps(
    {
        "summary": "经营现金流下降主要由预收货款下降所致",
        "reasons": [
            {"claim": "预收货款下降", "evidence_level": "disclosed", "refs": ["C1"], "basis": "附注明写"},
            {"claim": "经营现金流净额下降", "evidence_level": "inferred", "refs": ["F1"], "basis": "数据方向"},
        ],
        "caveats": ["推断类结论未获报告直接确认"],
    }
)


# -- build_evidence_context --------------------------------------------------
def test_build_evidence_context_maps_refs_and_filters_no_value():
    text, mapping = build_evidence_context(_pack())
    assert "[F1]" in text and "[C1]" in text
    assert mapping["F1"].ref_id == "ocf-2024"
    assert mapping["C1"].ref_id == "c1" and mapping["C1"].page == 45
    # 无值事实被过滤：不应出现 F2
    assert "F2" not in text


def test_build_evidence_context_chunk_budget():
    pack = _pack()
    pack.text_evidence = [
        EvidenceChunk(
            chunk_id=f"c{i}", report_id="r1", company_name="测试公司",
            fiscal_year=2024, text=f"证据{i}",
        )
        for i in range(1, 8)
    ]
    policy = ContextPolicy(max_evidence_chunks=5)
    text, mapping = build_evidence_context(pack, policy)
    assert "C5" in text and "C6" not in text
    assert "C6" not in mapping


# -- AttributionWriter -------------------------------------------------------
def test_writer_with_fake_invoke():
    writer = AttributionWriter(invoke=lambda system, user: _GOOD_JSON)
    draft = writer.write(_pack())
    assert draft is not None
    assert draft.conclusion.summary != ""
    assert [r.evidence_level for r in draft.conclusion.reasons] == [
        EvidenceLevel.DISCLOSED, EvidenceLevel.INFERRED,
    ]


def test_writer_extracts_json_from_markdown_fence():
    writer = AttributionWriter(invoke=lambda system, user: f"```json\n{_GOOD_JSON}\n```")
    draft = writer.write(_pack())
    assert draft is not None and len(draft.conclusion.reasons) == 2


def test_writer_invalid_level_returns_none():
    bad = json.dumps({"reasons": [{"claim": "x", "evidence_level": "bogus"}]})
    writer = AttributionWriter(invoke=lambda system, user: bad)
    assert writer.write(_pack()) is None


def test_writer_non_json_returns_none():
    writer = AttributionWriter(invoke=lambda system, user: "not a json")
    assert writer.write(_pack()) is None


def test_writer_no_key_returns_none():
    writer = AttributionWriter(api_key=None)
    assert writer.write(_pack()) is None


def test_writer_filters_out_of_range_refs():
    # refs 引用不存在的 F9，应被过滤，结论仍可用
    data = json.dumps(
        {"reasons": [{"claim": "x", "evidence_level": "disclosed", "refs": ["C1", "F9"]}]}
    )
    writer = AttributionWriter(invoke=lambda system, user: data)
    draft = writer.write(_pack())
    assert draft is not None
    assert draft.conclusion.reasons[0].refs == ["C1"]


def test_build_attribution_writer_key_gating(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert build_attribution_writer() is None
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert build_attribution_writer() is not None


# -- render_report ----------------------------------------------------------
def test_render_report_labels_and_sources():
    conclusion = AttributionConclusion(
        summary="经营现金流下降",
        reasons=[
            AttributionReason(claim="预收货款下降", evidence_level=EvidenceLevel.DISCLOSED, refs=["C1"], basis="附注明写"),
            AttributionReason(claim="现金储备减少", evidence_level=EvidenceLevel.INFERRED, refs=["F1"], basis="数据方向"),
        ],
        caveats=["推断未获直接确认"],
    )
    mapping = {
        "F1": SourceRef(kind="fact", ref_id="ocf-2024", report_id="r1"),
        "C1": SourceRef(kind="chunk", ref_id="c1", report_id="r1", page=45),
    }
    report, sources = render_report(AttributionDraft(conclusion, mapping))
    assert "明确披露" in report and "证据推断" in report
    assert "[C1]" in report and "[F1]" in report
    assert "来源" in report and "ocf-2024" in report and "p45" in report
    assert {s.ref_id for s in sources} == {"ocf-2024", "c1"}


# -- graph 集成 --------------------------------------------------------------
def _store():
    store = Store(":memory:")
    store.insert_financial_facts(
        [
            FinancialFact(
                fact_id="ocf-2024", report_id="r1", company_name="测试公司",
                fiscal_year=2024, raw_item="operating_cash_flow", standard_item="operating_cash_flow",
                value=180.0, unit="元", statement_type=StatementType.CASH_FLOW,
                period="2024", consolidation_scope="consolidated",
            ),
            FinancialFact(
                fact_id="revenue-2024", report_id="r1", company_name="测试公司",
                fiscal_year=2024, raw_item="revenue", standard_item="revenue",
                value=2000.0, unit="元", statement_type=StatementType.INCOME_STATEMENT,
                period="2024", consolidation_scope="consolidated",
            ),
        ]
    )
    return store


def _chunks():
    return [
        EvidenceChunk(
            chunk_id="c1", report_id="r1", company_name="测试公司", fiscal_year=2024,
            section_path="现金流量表附注", text="预收货款下降所致",
        )
    ]


def test_graph_attribution_uses_llm_writer():
    writer = AttributionWriter(invoke=lambda system, user: _GOOD_JSON)
    g = AgentGraph(_store(), chunks=_chunks(), attribution_writer=writer)
    out = g.run("为什么经营现金流下降", company_name="测试公司")
    assert out["status"] == "success"
    assert "## 归因分析" in out["final_answer"]
    assert "[明确披露]" in out["final_answer"]


def test_graph_writer_failure_falls_back_to_template():
    writer = AttributionWriter(invoke=lambda system, user: "not json")
    g = AgentGraph(_store(), chunks=_chunks(), attribution_writer=writer)
    out = g.run("为什么经营现金流下降", company_name="测试公司")
    assert out["status"] == "success"
    # 回退模板拼接（非归因报告）
    assert "## 归因分析" not in out["final_answer"]


def test_graph_simple_query_skips_llm():
    planner_calls: list[int] = []
    writer_calls: list[int] = []
    llm = LLMPlanner(invoke=lambda s, u, t: planner_calls.append(1) or [])
    writer = AttributionWriter(invoke=lambda s, u: writer_calls.append(1) or "")
    g = AgentGraph(_store(), chunks=_chunks(), llm_planner=llm, attribution_writer=writer)
    out = g.run("2024年营收多少", company_name="测试公司")
    assert out["status"] == "success"
    assert "revenue=2000.0" in out["final_answer"]
    assert planner_calls == []  # SIMPLE 路径不调 llm_planner
    assert writer_calls == []   # 非归因不调 writer
