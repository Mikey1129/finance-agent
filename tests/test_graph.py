"""agent.graph 状态图测试：三条路径 + 澄清短路。"""

from agent.graph import AgentGraph
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
