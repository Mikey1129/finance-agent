"""agent.planner 任务规划测试：三条路径的步骤序列。"""

from agent.planner import ComplexityLevel, Planner, assess_complexity
from agent.state import Intent


def test_fact_query_single_step_per_item():
    p = Planner().plan(
        Intent.FACT_QUERY, company_name="公牛集团", periods=["2024"],
        standard_items=["revenue"],
    )
    assert len(p) == 1
    assert p[0].tool == "query_financial_fact"
    assert p[0].args["standard_item"] == "revenue"
    assert p[0].args["fiscal_year"] == 2024


def test_metric_single_period_two_steps():
    p = Planner().plan(
        Intent.METRIC_ANALYSIS, company_name="c", periods=["2024"],
        metric_codes=["net_margin"],
    )
    assert [s.tool for s in p] == ["query_financial_fact", "compute_metric"]


def test_metric_multi_period_uses_compare():
    p = Planner().plan(
        Intent.METRIC_ANALYSIS, company_name="c", periods=["2023", "2024"],
        metric_codes=["net_margin"],
    )
    assert [s.tool for s in p] == ["compare_periods"]


def test_attribution_full_pipeline():
    p = Planner().plan(
        Intent.ATTRIBUTION, query="为什么经营现金流下降", company_name="c", periods=["2024"],
        metric_codes=["ocf_to_net_profit"],
    )
    assert [s.tool for s in p] == [
        "query_financial_fact",
        "compute_metric",
        "retrieve_evidence",
        "validate_result",
    ]
    assert p[2].args["query"] == "为什么经营现金流下降"


def test_clarify_no_plan():
    assert Planner().plan(Intent.CLARIFY, query="你好") == []


# -- 复杂度门控 --------------------------------------------------------------
def test_assess_complexity_attribution_is_complex():
    assert assess_complexity(Intent.ATTRIBUTION) is ComplexityLevel.COMPLEX


def test_assess_complexity_simple_intents_are_simple():
    for intent in (Intent.FACT_QUERY, Intent.METRIC_ANALYSIS, Intent.CLARIFY, None):
        assert assess_complexity(intent) is ComplexityLevel.SIMPLE
