"""agent.tools 工具注册与 Schema 测试：5 个核心工具的正常路径 + 边界。"""

import pytest

from agent import (
    ComputeMetricTool,
    ComparePeriodsTool,
    QueryFinancialFactTool,
    RetrieveEvidenceTool,
    ToolRegistry,
    ToolStatus,
    ValidateResultTool,
    build_tools,
)
from domain import EvidenceChunk, FinancialFact, StatementType
from finance.store import Store
from finance.validation import ValidationReport


def _store_with_facts() -> Store:
    """构造 2023/2024 两期 consolidated 标准 facts（数值人工可核验，同 test_metrics）。"""
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
                    fact_id=f"{item}-{year}",
                    report_id="r1",
                    company_name="测试公司",
                    fiscal_year=int(year),
                    raw_item=item,
                    standard_item=item,
                    value=v,
                    unit="元",
                    statement_type=st,
                    period=year,
                    consolidation_scope="consolidated",
                )
            )
    store.insert_financial_facts(facts)
    return store


# ---------------------------------------------------------------------------
# query_financial_fact
# ---------------------------------------------------------------------------
def test_query_by_item_and_scope():
    store = _store_with_facts()
    tool = QueryFinancialFactTool(store)
    r = tool(company_name="测试公司", standard_item="revenue", fiscal_year=2024)
    assert r.status is ToolStatus.SUCCESS
    assert len(r.data) == 1
    assert r.data[0].value == 2000.0
    assert r.data[0].unit == "元"
    assert r.sources[0].ref_id == "revenue-2024"


def test_query_no_match_returns_no_result():
    store = _store_with_facts()
    tool = QueryFinancialFactTool(store)
    r = tool(company_name="不存在公司")
    assert r.status is ToolStatus.NO_RESULT


def test_query_empty_company_invalid_param():
    store = _store_with_facts()
    tool = QueryFinancialFactTool(store)
    r = tool(company_name="  ")
    assert r.status is ToolStatus.ERROR
    assert r.error.type == "invalid_param"


# ---------------------------------------------------------------------------
# compute_metric
# ---------------------------------------------------------------------------
def test_compute_gross_margin():
    store = _store_with_facts()
    tool = ComputeMetricTool(store)
    r = tool(company_name="测试公司", fiscal_year=2024, metric_codes=["gross_margin"])
    assert r.status is ToolStatus.SUCCESS
    m = r.data["gross_margin"]
    assert m.computed
    assert m.value == pytest.approx((2000 - 1200) / 2000)


def test_compute_unknown_metric():
    store = _store_with_facts()
    tool = ComputeMetricTool(store)
    r = tool(company_name="测试公司", fiscal_year=2024, metric_codes=["nope"])
    assert r.status is ToolStatus.ERROR
    assert r.error.type == "unknown_metric"


def test_compute_no_data():
    store = _store_with_facts()
    tool = ComputeMetricTool(store)
    r = tool(company_name="测试公司", fiscal_year=1999, metric_codes=["roe"])
    assert r.status is ToolStatus.ERROR
    assert r.error.type == "insufficient_data"


# ---------------------------------------------------------------------------
# compare_periods
# ---------------------------------------------------------------------------
def test_compare_periods_series():
    store = _store_with_facts()
    tool = ComparePeriodsTool(store)
    r = tool(company_name="测试公司", metric_codes=["gross_margin"])
    assert r.status is ToolStatus.SUCCESS
    series = r.data["gross_margin"]
    assert [m.period for m in series] == ["2023", "2024"]
    assert series[0].value == pytest.approx((1600 - 1000) / 1600)
    assert series[1].value == pytest.approx((2000 - 1200) / 2000)


# ---------------------------------------------------------------------------
# retrieve_evidence
# ---------------------------------------------------------------------------
def _chunks():
    return [
        EvidenceChunk(
            chunk_id="c1", report_id="r1", company_name="公牛集团", fiscal_year=2024,
            section_path="三、财务报表", text="应收账款期末余额大幅增加",
        ),
        EvidenceChunk(
            chunk_id="c2", report_id="r1", company_name="公牛集团", fiscal_year=2024,
            section_path="管理层讨论与分析", text="公司主营业务收入保持稳定增长",
        ),
        EvidenceChunk(
            chunk_id="c3", report_id="r2", company_name="其他公司", fiscal_year=2024,
            section_path="三、财务报表", text="应收账款余额也很多",
        ),
    ]


def test_retrieve_rank_and_company_filter():
    store = Store(":memory:")
    tool = RetrieveEvidenceTool(store, _chunks())
    r = tool(query="应收账款", company_name="公牛集团")
    assert r.status is ToolStatus.SUCCESS
    ids = [c.chunk_id for c in r.data]
    assert ids[0] == "c1"
    assert "c3" not in ids


def test_retrieve_no_source():
    store = Store(":memory:")
    tool = RetrieveEvidenceTool(store)  # 未传 chunks
    r = tool(query="应收账款")
    assert r.status is ToolStatus.ERROR
    assert r.error.type == "no_evidence_source"


# ---------------------------------------------------------------------------
# validate_result
# ---------------------------------------------------------------------------
def test_validate_balance_ok():
    store = _store_with_facts()  # 1000 = 600 + 400 平衡
    tool = ValidateResultTool(store)
    r = tool(company_name="测试公司", check="balance")
    assert r.status is ToolStatus.SUCCESS
    assert isinstance(r.data, ValidationReport)
    assert r.data.ok()


def test_validate_unbalanced():
    store = Store(":memory:")
    store.insert_financial_facts(
        [
            FinancialFact(
                fact_id="a", report_id="r1", company_name="c", fiscal_year=2025,
                raw_item="total_assets", standard_item="total_assets", value=200.0,
                statement_type=StatementType.BALANCE_SHEET, period="2025",
            ),
            FinancialFact(
                fact_id="l", report_id="r1", company_name="c", fiscal_year=2025,
                raw_item="total_liabilities", standard_item="total_liabilities", value=100.0,
                statement_type=StatementType.BALANCE_SHEET, period="2025",
            ),
            FinancialFact(
                fact_id="e", report_id="r1", company_name="c", fiscal_year=2025,
                raw_item="total_equity", standard_item="total_equity", value=80.0,
                statement_type=StatementType.BALANCE_SHEET, period="2025",
            ),
        ]
    )
    tool = ValidateResultTool(store)
    r = tool(company_name="c", check="balance")
    assert r.status is ToolStatus.SUCCESS
    assert r.data.errors == 1


def test_validate_invalid_check():
    store = _store_with_facts()
    tool = ValidateResultTool(store)
    r = tool(company_name="测试公司", check="bogus")
    assert r.status is ToolStatus.ERROR
    assert r.error.type == "invalid_param"


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------
def test_registry_schemas_and_invoke():
    store = _store_with_facts()
    registry = build_tools(store)
    assert set(registry.names()) == {
        "query_financial_fact",
        "compute_metric",
        "compare_periods",
        "retrieve_evidence",
        "validate_result",
    }
    schemas = registry.schemas()
    assert len(schemas) == 5
    assert all("parameters" in s for s in schemas)

    r = registry.invoke("compute_metric", company_name="测试公司", fiscal_year=2024, metric_codes=["roe"])
    assert r.status is ToolStatus.SUCCESS


def test_registry_unknown_tool():
    registry = build_tools(_store_with_facts())
    r = registry.invoke("no_such_tool")
    assert r.status is ToolStatus.ERROR
    assert r.error.type == "unknown_tool"


def test_registry_invalid_param():
    registry = build_tools(_store_with_facts())
    r = registry.invoke("compute_metric")  # 缺必填参数
    assert r.status is ToolStatus.ERROR
    assert r.error.type == "invalid_param"
