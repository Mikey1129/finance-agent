"""agent.context 上下文管理测试：多轮消歧 + 预算裁剪。"""

from agent.context import ContextBuilder, ContextPolicy, ContextResolver
from agent.state import AgentState, Intent
from agent.tools import ToolResult, ToolStatus
from domain import EvidenceChunk


def _state(company="公牛集团", periods=None):
    return AgentState(
        thread_id="t", run_id="r", user_query="q",
        company_name=company, periods=periods or ["2023", "2024"],
    )


def _chunk(cid, text):
    return EvidenceChunk(chunk_id=cid, report_id="r", company_name="c", fiscal_year=2024, text=text)


# ---------------------------------------------------------------------------
# ContextResolver
# ---------------------------------------------------------------------------
def test_resolver_inherits_company_uses_new_period():
    rc = ContextResolver().resolve("2024 年营收多少", _state())
    assert rc.company_name == "公牛集团"  # 继承公司
    assert rc.periods == ["2024"]          # 新指令期间覆盖
    assert rc.intent is Intent.FACT_QUERY


def test_resolver_new_company_overrides():
    rc = ContextResolver().resolve(
        "宁德时代 2024 年营收多少", _state(company="公牛集团"),
        known_companies=["公牛集团", "宁德时代"],
    )
    assert rc.company_name == "宁德时代"  # 新指令公司覆盖


def test_resolver_near_n_years():
    rc = ContextResolver().resolve("近两年净利率变化", _state(periods=["2022", "2023", "2024"]))
    assert rc.intent is Intent.METRIC_ANALYSIS
    assert rc.periods == ["2023", "2024"]  # 最近 2 期


def test_resolver_clarify_when_no_company():
    rc = ContextResolver().resolve("营收多少", _state(company=None))
    assert rc.needs_clarification
    assert rc.clarification


# ---------------------------------------------------------------------------
# ContextBuilder
# ---------------------------------------------------------------------------
def test_builder_truncates_tool_results_and_evidence():
    st = _state()
    for i in range(8):
        st.add_tool_result(ToolResult(tool=f"t{i}", status=ToolStatus.SUCCESS))
    evidence = [_chunk(f"c{i}", "文本" * 50) for i in range(10)]
    rc = ContextResolver().resolve("营收", st)

    policy = ContextPolicy(max_evidence_chunks=3, max_tool_results=3)
    payload = ContextBuilder(policy).build(st, rc, evidence=evidence)
    assert len(payload.tool_results) == 3
    assert len(payload.evidence_chunks) == 3


def test_builder_crops_to_budget():
    st = _state()
    rc = ContextResolver().resolve("营收", st)
    evidence = [_chunk(f"c{i}", "很" * 500) for i in range(5)]  # 每条约 250 tokens
    policy = ContextPolicy(max_tokens=1000, max_evidence_chunks=5)
    payload = ContextBuilder(policy).build(st, rc, evidence=evidence)
    assert payload.within_budget(policy)
    assert len(payload.evidence_chunks) < 5  # 超预算被裁剪
