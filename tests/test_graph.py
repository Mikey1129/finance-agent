"""agent.graph 状态图测试：三条路径 + 澄清短路。"""

import json

from agent.attribution import AttributionWriter
from agent.graph import AgentGraph
from agent.reviewer import ConclusionReviewer
from domain import EvidenceChunk, FinancialFact, StatementType
from finance.store import Store


def _store():
    store = Store(":memory:")
    data = [
        ("total_assets", StatementType.BALANCE_SHEET, 800.0, 1000.0),
        ("total_liabilities", StatementType.BALANCE_SHEET, 500.0, 600.0),
        ("total_equity", StatementType.BALANCE_SHEET, 300.0, 400.0),
        ("total_current_assets", StatementType.BALANCE_SHEET, 400.0, 500.0),
        ("total_current_liabilities", StatementType.BALANCE_SHEET, 250.0, 300.0),
        ("inventory", StatementType.BALANCE_SHEET, 80.0, 100.0),
        ("accounts_receivable", StatementType.BALANCE_SHEET, 100.0, 120.0),
        ("revenue", StatementType.INCOME_STATEMENT, 1600.0, 2000.0),
        ("cost_of_revenue", StatementType.INCOME_STATEMENT, 1000.0, 1200.0),
        ("net_profit", StatementType.INCOME_STATEMENT, 150.0, 200.0),
        ("operating_cash_flow", StatementType.CASH_FLOW, 140.0, 180.0),
    ]
    facts = []
    for item, st, v2023, v2024 in data:
        for year, v in (("2023", v2023), ("2024", v2024)):
            facts.append(
                FinancialFact(
                    fact_id=f"{item}-{year}", report_id="r1", company_name="测试公司",
                    fiscal_year=int(year), raw_item=item, standard_item=item, value=v,
                    unit="元", statement_type=st, period=year, consolidation_scope="consolidated",
                )
            )
    store.insert_financial_facts(facts)
    return store


def _chunks():
    return [
        EvidenceChunk(
            chunk_id="c1", report_id="r1", company_name="测试公司", fiscal_year=2024,
            section_path="管理层讨论与分析", text="经营现金流下降主要因应收账款增加",
        )
    ]


def test_graph_fact_query():
    g = AgentGraph(_store())
    out = g.run("2024 年营收多少", company_name="测试公司")
    assert out["status"] == "success"
    assert "营业收入" in out["final_answer"] and "2,000.00" in out["final_answer"]


def test_graph_metric_analysis():
    g = AgentGraph(_store())
    out = g.run("2024 年毛利率", company_name="测试公司")
    assert out["status"] == "success"
    assert "毛利率" in out["final_answer"]


def test_graph_metric_analysis_without_period_uses_latest():
    # 无年份的指标问法（如「收益很高吗」）应自动用最新期间，而非崩溃/澄清
    g = AgentGraph(_store())
    out = g.run("收益很高吗", company_name="测试公司")
    assert out["status"] == "success"
    assert out["periods"] == ["2024"]  # 最新期间
    assert "净利率" in out["final_answer"]


def test_graph_clarify_short_circuits():
    g = AgentGraph(_store())
    out = g.run("你好")
    assert out["status"] == "need_clarification"
    assert out["needs_clarification"]


def test_graph_attribution_uses_evidence():
    g = AgentGraph(_store(), chunks=_chunks())
    out = g.run("为什么经营现金流下降", company_name="测试公司")
    assert out["status"] == "success"
    assert "检索到" in out["final_answer"]


def test_graph_metric_analysis_answer_omits_raw_facts():
    # 指标问法 answer 应只含指标结论（百分比格式），不列 query_financial_fact 的原始事实
    g = AgentGraph(_store())
    out = g.run("2024 年毛利率", company_name="测试公司")
    assert out["status"] == "success"
    assert "毛利率" in out["final_answer"]
    assert "40.00%" in out["final_answer"]  # 百分比格式化（而非 0.4000）
    # 去冗长：原始事实（「营业收入 = 2,000.00 元」等）不应出现在指标问法 answer 里
    assert "营业收入" not in out["final_answer"]
    assert "元" not in out["final_answer"]


def test_graph_attribution_calls_reviewer():
    # 注入 mock reviewer：ATTRIBUTION 路径应 write → review → render，review 被调用一次
    good_json = json.dumps(
        {
            "summary": "经营现金流下降",
            "reasons": [{"claim": "预收货款下降", "evidence_level": "disclosed", "refs": ["C1"], "basis": "附注"}],
            "caveats": [],
        }
    )
    writer_calls: list[int] = []
    reviewer_calls: list[int] = []
    writer = AttributionWriter(invoke=lambda s, u: writer_calls.append(1) or good_json)
    reviewer = ConclusionReviewer(invoke=lambda s, u: reviewer_calls.append(1) or good_json)
    g = AgentGraph(_store(), chunks=_chunks(), attribution_writer=writer, reviewer=reviewer)
    out = g.run("为什么经营现金流下降", company_name="测试公司")
    assert out["status"] == "success"
    assert "## 归因分析" in out["final_answer"]
    assert writer_calls == [1]
    assert reviewer_calls == [1]  # 输出前校验 gate 生效
