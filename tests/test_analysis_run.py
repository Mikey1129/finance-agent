"""AnalysisRun 落库测试：运行记录可回放、可查询。"""

from agent.graph import AgentGraph
from domain import AnalysisRun, FinancialFact, StatementType, ToolCallRecord
from finance.store import Store


def test_insert_and_get_analysis_run():
    store = Store(":memory:")
    run = AnalysisRun(
        run_id="r1", thread_id="t1", user_query="2024 年营收多少",
        company_name="测试公司", periods=["2024"], intent="fact_query",
        status="success",
        plan=[{"tool": "query_financial_fact", "args": {"standard_item": "revenue"}}],
        tool_calls=[
            ToolCallRecord(
                tool="query_financial_fact", status="success",
                summary="revenue=2000.0", source_refs=["fact:revenue-2024"],
            )
        ],
        final_answer="revenue=2000.0",
        rule_version="deterministic-v1",
    )
    store.insert_analysis_run(run)
    assert store.count_analysis_runs() == 1

    got = store.get_analysis_run("r1")
    assert got["status"] == "success"
    assert got["company_name"] == "测试公司"
    assert got["periods"] == ["2024"]
    assert got["plan"] == [{"tool": "query_financial_fact", "args": {"standard_item": "revenue"}}]
    assert got["tool_calls"][0]["source_refs"] == ["fact:revenue-2024"]
    assert got["rule_version"] == "deterministic-v1"


def test_query_analysis_runs_by_thread():
    store = Store(":memory:")
    for i in ("a", "b"):
        store.insert_analysis_run(
            AnalysisRun(run_id=f"r{i}", thread_id="t1", user_query="q", status="success")
        )
    store.insert_analysis_run(
        AnalysisRun(run_id="rc", thread_id="t2", user_query="q", status="success")
    )
    assert store.count_analysis_runs() == 3
    assert len(store.query_analysis_runs(thread_id="t1")) == 2
    assert store.query_analysis_runs(company_name="不存在的公司") == []


def _store():
    store = Store(":memory:")
    store.insert_financial_facts(
        [
            FinancialFact(
                fact_id=f"revenue-{y}", report_id="r1", company_name="测试公司",
                fiscal_year=y, raw_item="revenue", standard_item="revenue", value=v,
                unit="元", statement_type=StatementType.INCOME_STATEMENT,
                period=str(y), consolidation_scope="consolidated",
            )
            for y, v in ((2023, 1600.0), (2024, 2000.0))
        ]
    )
    return store


def test_graph_records_run():
    store = _store()
    g = AgentGraph(store, record_runs=True)
    out = g.run("2024 年营收多少", company_name="测试公司", thread_id="t1")
    assert out["status"] == "success"

    assert store.count_analysis_runs() == 1
    runs = store.query_analysis_runs(thread_id="t1")
    assert runs[0]["company_name"] == "测试公司"
    assert runs[0]["status"] == "success"
    assert runs[0]["tool_calls"][0]["tool"] == "query_financial_fact"
    assert runs[0]["rule_version"] == "deterministic-v1"
    assert runs[0]["final_answer"]
