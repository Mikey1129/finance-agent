"""agent.llm LLM 决策层测试：解析容错 + 注入 fake 规划 + graph 集成。"""

from agent.graph import AgentGraph
from agent.llm import LLMPlanner, PlannedCall, parse_tool_calls
from domain import FinancialFact, StatementType
from finance.store import Store


# -- parse_tool_calls 解析与容错 ----------------------------------------------
def test_parse_tool_calls_dict():
    calls = parse_tool_calls(
        [{"name": "query_financial_fact", "args": {"standard_item": "revenue"}}]
    )
    assert calls == [PlannedCall(tool="query_financial_fact", args={"standard_item": "revenue"})]


def test_parse_tool_calls_json_string_args():
    calls = parse_tool_calls([{"name": "t", "args": '{"a": 1}'}])
    assert calls[0].args == {"a": 1}


def test_parse_tool_calls_bad_json_falls_back_empty():
    calls = parse_tool_calls([{"name": "t", "args": "not-json"}])
    assert calls[0].args == {}


def test_parse_tool_calls_skips_empty_name():
    calls = parse_tool_calls([{"name": "", "args": {}}, {"name": "t2", "args": {}}])
    assert [c.tool for c in calls] == ["t2"]


def test_parse_tool_calls_non_dict_args():
    calls = parse_tool_calls([{"name": "t", "args": 123}])
    assert calls[0].args == {}


# -- LLMPlanner 注入 fake -----------------------------------------------------
def test_llm_planner_plan_with_fake_invoke():
    planner = LLMPlanner(
        invoke=lambda system, user, tools: [
            {"name": "compute_metric", "args": {"metric_codes": ["roe"]}}
        ]
    )
    calls = planner.plan(
        "算一下 ROE",
        [{"name": "compute_metric", "description": "d", "parameters": {}}],
        company_name="测试公司",
    )
    assert [c.tool for c in calls] == ["compute_metric"]


# -- graph 集成：ATTRIBUTION 走 LLM 规划而非规则 Planner -----------------------
def _store():
    store = Store(":memory:")
    store.insert_financial_facts(
        [
            FinancialFact(
                fact_id="revenue-2024", report_id="r1", company_name="测试公司",
                fiscal_year=2024, raw_item="revenue", standard_item="revenue", value=2000.0,
                unit="元", statement_type=StatementType.INCOME_STATEMENT,
                period="2024", consolidation_scope="consolidated",
            )
        ]
    )
    return store


def test_graph_attribution_uses_llm_planner():
    # 规则 Planner 对 ATTRIBUTION 会生成 4 步；LLM 只返回 1 步，据此区分
    llm = LLMPlanner(
        invoke=lambda system, user, tools: [
            {"name": "query_financial_fact", "args": {"company_name": "测试公司", "fiscal_year": 2024}}
        ]
    )
    g = AgentGraph(_store(), llm_planner=llm)
    out = g.run("为什么经营现金流下降", company_name="测试公司")
    assert out["status"] == "success"
    assert len(out["tool_results"]) == 1
    assert out["tool_results"][0].tool == "query_financial_fact"


def test_graph_no_llm_planner_falls_back_to_rules():
    # 未注入 LLM 时，ATTRIBUTION 仍走规则 Planner（4 步）
    g = AgentGraph(_store())
    out = g.run("为什么经营现金流下降", company_name="测试公司")
    assert len(out["tool_results"]) == 4


# -- LLM 兜底：规则未命中（CLARIFY）时交给 LLM 理解 ---------------------------------
def test_graph_llm_fallback_on_clarify():
    # 规则 CLARIFY 的口语问法，靠 LLM 兜底落到工具调用（而非返回「无法理解」）
    llm = LLMPlanner(
        invoke=lambda system, user, tools: [
            {"name": "query_financial_fact", "args": {"company_name": "错误公司", "fiscal_year": 2024}}
        ]
    )
    g = AgentGraph(_store(), llm_planner=llm)
    out = g.run("这公司的财务状况健康吗", company_name="测试公司")
    assert out["status"] == "success"
    assert len(out["tool_results"]) == 1
    assert out["tool_results"][0].tool == "query_financial_fact"


def test_graph_llm_fallback_forces_resolved_company():
    # 公司名以规则消歧结果为准，覆盖 LLM 填错的公司名
    llm = LLMPlanner(
        invoke=lambda system, user, tools: [
            {"name": "query_financial_fact", "args": {"company_name": "错误公司", "fiscal_year": 2024}}
        ]
    )
    g = AgentGraph(_store(), llm_planner=llm)
    out = g.run("这公司的财务状况健康吗", company_name="测试公司")
    # 结果应来自「测试公司」，而非 LLM 填的「错误公司」
    assert out["tool_results"][0].data
    assert all(f.company_name == "测试公司" for f in out["tool_results"][0].data)


def test_graph_llm_fallback_keeps_clarify_when_no_tools():
    # LLM 也没给出工具调用 → 维持规则澄清，不硬凑答案
    llm = LLMPlanner(invoke=lambda system, user, tools: [])
    g = AgentGraph(_store(), llm_planner=llm)
    out = g.run("随便聊聊天气", company_name="测试公司")
    assert out["status"] == "need_clarification"
    assert out["needs_clarification"]


def test_graph_rule_hit_skips_llm_fallback():
    # 规则已命中的问法不触发 LLM 兜底（规则优先，快路径零成本）
    llm = LLMPlanner(
        invoke=lambda system, user, tools: [{"name": "validate_result", "args": {}}]
    )
    g = AgentGraph(_store(), llm_planner=llm)
    out = g.run("2024 年营收多少", company_name="测试公司")
    tools = [r.tool for r in out["tool_results"]]
    assert "validate_result" not in tools
    assert "query_financial_fact" in tools
