"""agent.skills 评测样例（方案 5.7「每个 Skill 至少包含评测样例」）。

构造一个两期（2023/2024）的合成财报，验证每个 Skill 能正确组合工具、
产出结构化结论，且校验规则（sanity 检查）在异常时触发 warning。
"""

from domain import EvidenceChunk, FinancialFact, StatementType
from finance.store import Store
from agent.skills import build_skills, list_skill_meta


def _store() -> Store:
    """合成财报：两期，覆盖三大报表 + 计算指标所需科目。"""
    store = Store(":memory:")
    data = [
        # (科目, 报表类型, 2023 值, 2024 值)
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
            section_path="管理层讨论与分析", text="经营活动现金流量净额增长主要因销售回款增加",
        )
    ]


def _skills():
    return build_skills(_store(), _chunks())


def test_list_skill_meta_has_five():
    meta = list_skill_meta()
    assert len(meta) == 5
    codes = {m["code"] for m in meta}
    assert codes == {
        "financial_statement_analysis", "profitability_analysis",
        "cash_flow_analysis", "financial_risk_analysis", "annual_report_summary",
    }
    # 每个 Skill 都含方案 5.7 要求的元信息
    assert all(m["name"] and m["description"] and m["applicable"] for m in meta)


def test_financial_statement_analysis():
    r = _skills()["financial_statement_analysis"].run("测试公司", 2024)
    assert r.status == "success"
    assert "营收" in r.summary and "平衡" in r.summary
    kinds = {s.kind for s in r.sections}
    assert kinds == {"facts", "validation"}


def test_profitability_analysis():
    r = _skills()["profitability_analysis"].run("测试公司", 2024)
    assert r.status == "success"
    metrics = r.sections[0].rows
    codes = {m["code"] for m in metrics}
    assert codes == {"gross_margin", "net_margin", "roa", "roe"}
    # 毛利率 = (2000-1200)/2000 = 0.4
    gm = next(m for m in metrics if m["code"] == "gross_margin")
    assert abs(gm["value"] - 0.4) < 1e-6


def test_cash_flow_analysis_flags_divergence():
    r = _skills()["cash_flow_analysis"].run("测试公司", 2024)
    assert r.status == "success"
    # 净利润 200 - 经营现金流 180 = 20 > 0，触发背离信号
    assert any("背离" in w for w in r.warnings)


def test_financial_risk_analysis_signals():
    r = _skills()["financial_risk_analysis"].run("测试公司", 2024)
    assert r.status == "success"
    kinds = {s.kind for s in r.sections}
    assert kinds == {"metrics", "signals"}
    # 资产负债率 = 600/1000 = 0.6 < 0.7，不应触发偿债 warning
    assert not any("资产负债率" in w for w in r.warnings)


def test_annual_report_summary():
    r = _skills()["annual_report_summary"].run("测试公司", 2024)
    assert r.status == "success"
    assert any(s.kind == "facts" for s in r.sections)
    assert any(s.kind == "metrics" for s in r.sections)


def test_skill_no_data_is_insufficient():
    empty = build_skills(Store(":memory:"), [])
    r = empty["profitability_analysis"].run("无数据公司", 2024)
    assert r.status == "insufficient"
