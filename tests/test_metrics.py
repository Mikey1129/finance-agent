"""metrics 指标注册中心测试：公式正确性 + 边界条件（分母为 0 / 缺科目 / 无上期 / scope 过滤）。"""

from pathlib import Path

import pytest

from domain import FinancialFact, StatementType
from evaluation.eval_metrics import eval_metrics
from finance.metrics import (
    MetricStatus,
    compute_metric,
    compute_series,
    METRIC_DEFINITIONS,
)

_STORAGE = Path(__file__).resolve().parent.parent / "storage"
_REAL_PDF = _STORAGE / "公牛集团-2024年报.pdf"


def _make_facts():
    """构造 2023/2024 两期、consolidated 范围的标准 facts（数值人工可核验）。"""
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
                    statement_type=st,
                    period=year,
                    consolidation_scope="consolidated",
                )
            )
    return facts


def test_all_16_metrics_registered():
    assert len(METRIC_DEFINITIONS) == 16
    # 6 组分组齐全
    groups = {d.group for d in METRIC_DEFINITIONS.values()}
    assert groups == {"成长能力", "盈利能力", "偿债能力", "现金流", "运营效率", "财务质量"}


def test_ratio_metrics():
    facts = _make_facts()
    cases = {
        "gross_margin": 0.4,          # (2000-1200)/2000
        "net_margin": 0.1,            # 200/2000
        "roa": 0.2,                   # 200/1000
        "roe": 0.5,                   # 200/400
        "debt_to_asset_ratio": 0.6,   # 600/1000
        "current_ratio": 500 / 300,
        "quick_ratio": 400 / 300,     # (500-100)/300
        "ocf_to_net_profit": 0.9,     # 180/200
    }
    for code, expected in cases.items():
        r = compute_metric(code, facts, "2024")
        assert r.computed, f"{code} 应可计算"
        assert r.value == pytest.approx(expected), code


def test_growth_metrics():
    facts = _make_facts()
    r = compute_metric("revenue_growth", facts, "2024")
    assert r.computed
    assert r.value == pytest.approx(0.25)          # 400/1600

    r2 = compute_metric("net_profit_growth", facts, "2024")
    assert r2.value == pytest.approx(50 / 150)


def test_turnover_metrics():
    facts = _make_facts()
    r = compute_metric("accounts_receivable_turnover", facts, "2024")
    assert r.computed
    assert r.value == pytest.approx(2000 / 110)    # 2000 / ((120+100)/2)

    r2 = compute_metric("inventory_turnover", facts, "2024")
    assert r2.value == pytest.approx(1200 / 90)    # 1200 / ((100+80)/2)


def test_change_and_raw_and_divergence():
    facts = _make_facts()
    assert compute_metric("accounts_receivable_change", facts, "2024").value == pytest.approx(20.0)
    assert compute_metric("inventory_change", facts, "2024").value == pytest.approx(20.0)
    assert compute_metric("operating_cash_flow", facts, "2024").value == pytest.approx(180.0)
    assert compute_metric("profit_cash_flow_divergence", facts, "2024").value == pytest.approx(20.0)


def test_divide_by_zero_returns_no_fake_number():
    """分母为 0 时不返回看似正常的数，而是 DIVIDE_BY_ZERO。"""
    facts = _make_facts()
    # 把 2024 营业收入改为 0
    for f in facts:
        if f.standard_item == "revenue" and f.period == "2024":
            f.value = 0.0
    r = compute_metric("net_margin", facts, "2024")
    assert r.status is MetricStatus.DIVIDE_BY_ZERO
    assert r.value is None


def test_insufficient_data_when_missing_item():
    """缺分母科目时返回 insufficient_data。"""
    facts = [f for f in _make_facts() if f.standard_item != "revenue"]
    r = compute_metric("net_margin", facts, "2024")
    assert r.status is MetricStatus.INSUFFICIENT_DATA
    assert r.value is None


def test_growth_requires_prev_period():
    """最早期间无上期，增长率应 insufficient_data（不编造）。"""
    facts = _make_facts()
    r = compute_metric("revenue_growth", facts, "2023")
    assert r.status is MetricStatus.INSUFFICIENT_DATA


def test_scope_isolation():
    """scope 过滤：consolidated 的事实不应被 parent_only 指标误取。"""
    facts = _make_facts()
    r = compute_metric("net_margin", facts, "2024", scope="parent_only")
    assert r.status is MetricStatus.INSUFFICIENT_DATA


def test_compute_series_cross_period():
    facts = _make_facts()
    series = compute_series("net_margin", facts)
    assert [r.period for r in series] == ["2023", "2024"]
    assert series[0].value == pytest.approx(150 / 1600)
    assert series[1].value == pytest.approx(200 / 2000)


@pytest.mark.skipif(not _REAL_PDF.exists(), reason="真实年报 PDF 未在本地")
def test_real_pdf_metrics_in_reasonable_range():
    """评测 B 端到端锚定：公牛集团关键指标应落在合理区间（防科目映射污染）。"""
    result = eval_metrics(str(_REAL_PDF), "公牛集团", 2024, scope="consolidated")
    m = result.metrics

    # 毛利率：公牛真实约 43%，区间防「营业总成本」误当「营业成本」（误映射会跌到 26%）
    assert m["gross_margin"].value == pytest.approx(0.43, abs=0.05)
    assert 0.2 < m["net_margin"].value < 0.4
    assert 0.0 < m["debt_to_asset_ratio"].value < 1.0
    assert m["current_ratio"].value > 1.0

    # 真实财报数据齐全，16 个指标都应算出来（无 insufficient_data / divide_by_zero）
    assert result.computed_count == result.total
