"""pdf_parser 跨页 period 继承与整表级平衡评测测试。"""

from pathlib import Path

import pytest

from domain import StatementType
from evaluation.eval_balance_sheet import eval_balance_sheet
from ingestion.excel_parser import parse_tabular
from ingestion.pdf_parser import _find_header_idx

_STORAGE = Path(__file__).resolve().parent.parent / "storage"
_REAL_PDF = _STORAGE / "公牛集团-2024年报.pdf"


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
