"""agent.router 意图分流测试：A/B/C/CLARIFY + 实体提取。"""

from agent import Intent, RouterResult, route
from agent.state import AgentState, AgentStatus


def test_route_fact_query():
    r = route("公牛集团 2024 年营业收入是多少", known_companies=["公牛集团"])
    assert r.intent is Intent.FACT_QUERY
    assert r.company_name == "公牛集团"
    assert r.periods == ["2024"]
    assert "revenue" in r.standard_items


def test_route_metric_analysis():
    r = route("公牛集团近三年净利率变化", known_companies=["公牛集团"])
    assert r.intent is Intent.METRIC_ANALYSIS
    assert "net_margin" in r.metric_codes
    assert r.year_span == 3


def test_route_attribution():
    r = route("为什么公牛集团经营现金流下降了", known_companies=["公牛集团"])
    assert r.intent is Intent.ATTRIBUTION
    assert "operating_cash_flow" in r.standard_items


def test_route_attribution_wins_over_metric():
    # 归因词优先级最高，即使同时含指标词
    r = route("为什么净利率下降", known_companies=["公牛集团"])
    assert r.intent is Intent.ATTRIBUTION


def test_route_metric_over_fact():
    # 含指标名优先于数值查询词
    r = route("毛利率多少", known_companies=["公牛集团"])
    assert r.intent is Intent.METRIC_ANALYSIS
    assert "gross_margin" in r.metric_codes


def test_route_unknown_clarify():
    r = route("你好")
    assert r.intent is Intent.CLARIFY
    assert r.needs_clarification


def test_route_missing_company_needs_clarification():
    r = route("2024 年营收多少")  # 无公司名
    assert r.intent is Intent.FACT_QUERY
    assert r.needs_clarification
    assert r.clarification


def test_route_roe_alias():
    r = route("公牛集团 ROE 是多少", known_companies=["公牛集团"])
    assert r.intent is Intent.METRIC_ANALYSIS
    assert "roe" in r.metric_codes


def test_route_profitability_phrase():
    # 「收益很高吗」这类口语问法 → 盈利能力指标组，而非 CLARIFY
    r = route("公牛集团的收益很高吗", known_companies=["公牛集团"])
    assert r.intent is Intent.METRIC_ANALYSIS
    assert r.metric_codes == ["gross_margin", "net_margin", "roa", "roe"]
    assert not r.needs_clarification


def test_route_profitability_phrase_does_not_hijack_exact_item():
    # 精确科目（净利润/其他收益）仍走 FACT_QUERY，不被盈利能力口语词误伤
    r = route("公牛集团 2024 年净利润是多少", known_companies=["公牛集团"])
    assert r.intent is Intent.FACT_QUERY
    assert "net_profit" in r.standard_items
    r2 = route("公牛集团其他收益是多少", known_companies=["公牛集团"])
    assert r2.intent is Intent.FACT_QUERY
    assert "other_income" in r2.standard_items


# ---------------------------------------------------------------------------
# AgentState
# ---------------------------------------------------------------------------
def test_agent_state_defaults():
    st = AgentState(thread_id="t1", run_id="r1", user_query="q")
    assert st.status is AgentStatus.IDLE
    assert st.intent is None
    assert st.tool_results == []
    assert st.retry_count == 0


def test_agent_state_add_tool_result():
    from agent.tools import ToolResult, ToolStatus

    st = AgentState(thread_id="t1", run_id="r1", user_query="q")
    st.add_tool_result(ToolResult(tool="query_financial_fact", status=ToolStatus.SUCCESS))
    assert len(st.tool_results) == 1
