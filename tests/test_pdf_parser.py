"""pdf_parser 跨页 period 继承与整表级平衡评测测试。"""

from pathlib import Path

import pytest

from domain import StatementType
from evaluation.eval_balance_sheet import eval_balance_sheet
from ingestion.excel_parser import parse_tabular
from ingestion.pdf_parser import _find_header_idx, _match_title, parse_pdf

_STORAGE = Path(__file__).resolve().parent.parent / "storage"
_REAL_PDF = _STORAGE / "公牛集团-2024年报.pdf"
_PROSPECTUS_PDF = _STORAGE / "长鑫科技-招股书.pdf"


def test_find_header_idx_detects_header_row():
    header_table = [
        ["项目", "附注", "2024年12月31日", "2023年12月31日"],
        ["货币资金", "", "5,000", "4,000"],
    ]
    assert _find_header_idx(header_table) == 0


def test_find_header_idx_returns_none_for_continuation():
    """续页表首行是数据、列头无年份无标记 → 应返回 None（由调用方继承表头）。"""
    continuation = [
        ["资产总计", "", "20,494,034,490.68", "19,762,205,724.93"],
        ["负债合计", "", "4,619,054,119.92", "5,301,794,179.20"],
    ]
    assert _find_header_idx(continuation) is None


def test_find_header_idx_detects_annotation_header():
    """text 策略下表头年份被拆乱（「25年12月31日 20」），附注列标题仍可定位表头。

    资产负债表表头首列是「资产」（不在 _HEADER_MARKERS），且年份被拆乱导致
    extract_period 失效，此时靠「附注 20」列标题识别表头；数据行附注是「(五)1」编号。
    """
    table = [
        ["资产", "附注 20", "25年12月31日 20", "24年12月31日 20", "23年12月31日"],
        ["货币资金", "(五)1", "51,990", "42,698", "31,373"],
    ]
    assert _find_header_idx(table) == 0


def test_match_title_exact_not_substring():
    """标题精确匹配 + 短前缀容错；正文长词不得误命中（招股书正文常见）。"""
    assert _match_title("合并资产负债表") == (StatementType.BALANCE_SHEET, "consolidated")
    assert _match_title("母公司利润表") == (StatementType.INCOME_STATEMENT, "parent_only")
    assert _match_title("合并所有者权益变动表") == (None, None)  # 非主表标题
    assert _match_title("在合并资产负债表中股东/所") is None  # 正文长词不误命中


def test_period_inheritance_via_parse_tabular():
    """续页表用上一表头（含年份）解析全部数据行，期间应正确归到 2024/2023。"""
    headers = ["项目", "附注", "2024年12月31日", "2023年12月31日"]
    continuation_rows = [
        ["资产总计", "", "20,494,034,490.68", "19,762,205,724.93"],
        ["负债合计", "", "4,619,054,119.92", "5,301,794,179.20"],
        ["所有者权益合计", "", "15,874,980,370.76", "14,460,411,545.73"],
    ]
    facts = parse_tabular(
        headers,
        continuation_rows,
        "测试公司",
        2024,
        StatementType.BALANCE_SHEET,
        "r1",
        unit="元",
        consolidation_scope="consolidated",
    )
    assets_2024 = next(
        f for f in facts if f.standard_item == "total_assets" and f.period == "2024"
    )
    assert assets_2024.value == 20494034490.68
    assert assets_2024.period == "2024"
    # 两期 3 科目 = 6 条
    assert len(facts) == 6
    assert {f.period for f in facts} == {"2024", "2023"}


def test_parse_tabular_fact_id_includes_scope_and_row_offset():
    """fact_id 必须区分合并范围（scope）与跨页行偏移（row_offset），否则主键冲突。"""
    headers = ["项目", "2024年12月31日"]
    rows = [["货币资金", "100.00"]]
    a = parse_tabular(
        headers, rows, "公司", 2024, StatementType.BALANCE_SHEET, "r1",
        unit="元", consolidation_scope="consolidated",
    )
    b = parse_tabular(
        headers, rows, "公司", 2024, StatementType.BALANCE_SHEET, "r1",
        unit="元", consolidation_scope="parent_only",
    )
    c = parse_tabular(
        headers, rows, "公司", 2024, StatementType.BALANCE_SHEET, "r1",
        unit="元", consolidation_scope="consolidated", row_offset=5,
    )
    assert "consolidated" in a[0].fact_id
    assert "parent_only" in b[0].fact_id
    assert a[0].fact_id != b[0].fact_id, "合并/母公司报表 fact_id 必须区分"
    assert a[0].fact_id != c[0].fact_id, "跨页行偏移必须计入 fact_id"


@pytest.mark.skipif(not _REAL_PDF.exists(), reason="真实年报 PDF 未在本地")
def test_real_pdf_consolidated_balance_sheet_balanced():
    """整表级评测：公牛集团 2024 年报合并资产负债表两期均应平衡。

    真实数据（元）：资产 20,494,034,490.68 = 负债 4,619,054,119.92 + 权益 15,874,980,370.76。
    """
    result = eval_balance_sheet(str(_REAL_PDF), "公牛集团", 2024, scope="consolidated")
    assert result.total_errors == 0

    by_period = {p.period: p for p in result.periods}
    for period in ("2024", "2023"):
        assert period in by_period, f"缺少 {period} 期合并资产负债表"
        p = by_period[period]
        assert p.complete, f"{period} 期缺少科目: {p}"
        assert p.balanced, f"{period} 期不平衡: 资产={p.assets} 负债+权益={p.liabilities} + {p.equity}"

    # 精确数值锚点（防止静默退化）
    p2024 = by_period["2024"]
    assert p2024.assets == pytest.approx(20_494_034_490.68)
    assert p2024.liabilities == pytest.approx(4_619_054_119.92)
    assert p2024.equity == pytest.approx(15_874_980_370.76)


@pytest.mark.skipif(not _REAL_PDF.exists(), reason="真实年报 PDF 未在本地")
def test_real_pdf_statements_are_separated():
    """表格筛选：利润表/现金流量表应只含各自科目，不再与资产负债表混同。"""
    from finance.taxonomy import STANDARD_ITEMS

    income = parse_pdf(str(_REAL_PDF), "公牛集团", 2024, StatementType.INCOME_STATEMENT)
    cash = parse_pdf(str(_REAL_PDF), "公牛集团", 2024, StatementType.CASH_FLOW)

    income_items = {f.standard_item for f in income.facts if f.standard_item in STANDARD_ITEMS}
    cash_items = {f.standard_item for f in cash.facts if f.standard_item in STANDARD_ITEMS}

    # 利润表应含营业收入/净利润，且不含资产负债表科目
    assert "revenue" in income_items
    assert "net_profit" in income_items
    assert "total_assets" not in income_items
    assert "cash_and_equivalents" not in income_items

    # 现金流量表应含经营现金流净额，且不含利润表/资产负债表科目
    assert "operating_cash_flow" in cash_items
    assert "revenue" not in cash_items
    assert "total_assets" not in cash_items


@pytest.mark.skipif(not _REAL_PDF.exists(), reason="真实年报 PDF 未在本地")
def test_real_pdf_fact_ids_are_unique():
    """fact_id 唯一性：公牛年报含合并+母公司三张表且跨页，fact_id 不得重复（防主键冲突回归）。"""
    for st in (StatementType.BALANCE_SHEET, StatementType.INCOME_STATEMENT, StatementType.CASH_FLOW):
        result = parse_pdf(str(_REAL_PDF), "公牛集团", 2024, st)
        ids = [f.fact_id for f in result.facts]
        assert len(ids) == len(set(ids)), (
            f"{st.value} 存在重复 fact_id（{len(ids) - len(set(ids))} 个）"
        )
        assert len(ids) > 0


# ---------------------------------------------------------------------------
# 长鑫科技招股书（回归矩阵第二样本）
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _PROSPECTUS_PDF.exists(), reason="长鑫招股书 PDF 未在本地")
def test_real_prospectus_consolidated_balance_sheet_balanced():
    """整表级评测：长鑫科技招股书合并资产负债表三期均应平衡。

    招股书含合并+母公司两套报表、数值列带数字对齐竖线、列头常被 text 策略
    拆乱（「25年12月31日 20」），是 parse_pdf 通用化修复的核心回归样本。
    真实数据（元，与媒体披露交叉验证一致）：
    2025 资产 336,784,922,216.18 = 负债 182,684,982,794.10 + 权益 154,099,939,422.08。
    """
    result = eval_balance_sheet(str(_PROSPECTUS_PDF), "长鑫科技", 2025, scope="consolidated")
    assert result.total_errors == 0

    by_period = {p.period: p for p in result.periods}
    for period in ("2025", "2024", "2023"):
        assert period in by_period, f"缺少 {period} 期合并资产负债表"
        p = by_period[period]
        assert p.complete, f"{period} 期缺少科目: {p}"
        assert p.balanced, f"{period} 期不平衡: 资产={p.assets} 负债+权益={p.liabilities} + {p.equity}"

    # 精确数值锚点（防止静默退化）
    anchors = {
        "2025": (336_784_922_216.18, 182_684_982_794.10, 154_099_939_422.08),
        "2024": (271_598_831_026.52, 167_341_953_553.94, 104_256_877_472.58),
        "2023": (192_756_332_790.52, 127_642_949_977.86, 65_113_382_812.66),
    }
    for period, (assets, liab, equity) in anchors.items():
        p = by_period[period]
        assert p.assets == pytest.approx(assets), f"{period} 资产总计锚点失配"
        assert p.liabilities == pytest.approx(liab), f"{period} 负债合计锚点失配"
        assert p.equity == pytest.approx(equity), f"{period} 所有者权益锚点失配"


@pytest.mark.skipif(not _PROSPECTUS_PDF.exists(), reason="长鑫招股书 PDF 未在本地")
def test_real_prospectus_statements_are_separated():
    """表格筛选：招股书利润表/现金流量表应只含各自科目（回归：曾错解析 19698 条）。"""
    from finance.taxonomy import STANDARD_ITEMS

    income = parse_pdf(str(_PROSPECTUS_PDF), "长鑫科技", 2025, StatementType.INCOME_STATEMENT)
    cash = parse_pdf(str(_PROSPECTUS_PDF), "长鑫科技", 2025, StatementType.CASH_FLOW)

    income_items = {f.standard_item for f in income.facts if f.standard_item in STANDARD_ITEMS}
    cash_items = {f.standard_item for f in cash.facts if f.standard_item in STANDARD_ITEMS}

    assert "revenue" in income_items
    assert "net_profit" in income_items
    assert "total_assets" not in income_items
    assert "cash_and_equivalents" not in income_items

    assert "operating_cash_flow" in cash_items
    assert "revenue" not in cash_items
    assert "total_assets" not in cash_items


@pytest.mark.skipif(not _PROSPECTUS_PDF.exists(), reason="长鑫招股书 PDF 未在本地")
def test_real_prospectus_fact_ids_are_unique():
    """fact_id 唯一性：招股书合并+母公司两套报表且跨页，fact_id 不得重复。

    招股书每张表都有「合并」与「母公司」两套，同科目同位置仅靠 consolidation_scope
    与跨页行偏移区分，是主键冲突的最高危场景。
    """
    result = parse_pdf(str(_PROSPECTUS_PDF), "长鑫科技", 2025, StatementType.BALANCE_SHEET)
    ids = [f.fact_id for f in result.facts]
    assert len(ids) == len(set(ids)), (
        f"存在重复 fact_id（{len(ids) - len(set(ids))} 个）"
    )
    assert len(ids) > 0
    # 两套报表范围都应被识别（合并 + 母公司），否则 fact_id 唯一性检查失去意义
    scopes = {f.consolidation_scope for f in result.facts}
    assert "consolidated" in scopes and "parent_only" in scopes
