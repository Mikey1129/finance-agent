"""excel_parser 数值解析与表格解析测试。"""

from domain import DataStatus, StatementType
from ingestion.excel_parser import (
    _detect_item_col,
    _is_value_column,
    detect_unit,
    extract_period,
    parse_number,
    parse_tabular,
)


def test_parse_number_negative():
    assert parse_number("-123")[0] == -123.0
    assert parse_number("(456)")[0] == -456.0
    assert parse_number("（78）")[0] == -78.0


def test_parse_number_thousands_and_symbols():
    assert parse_number("1,234")[0] == 1234.0
    assert parse_number("$ 2457")[0] == 2457.0


def test_parse_number_status():
    assert parse_number("")[1] is DataStatus.MISSING
    assert parse_number("--")[1] is DataStatus.NOT_DISCLOSED
    assert parse_number("不适用")[1] is DataStatus.NOT_APPLICABLE
    assert parse_number("0")[1] is DataStatus.ZERO
    assert parse_number("abc")[1] is DataStatus.PARSE_ERROR


def test_detect_unit():
    assert detect_unit("单位：万元") == "万元"
    assert detect_unit("（单位：千元）") == "千元"
    assert detect_unit("in millions") == "millions"


def test_extract_period():
    assert extract_period("2025年") == "2025"
    assert extract_period("2024-12-31") == "2024"
    assert extract_period("无年份") is None


def test_parse_tabular_basic():
    headers = ["项目", "2025年", "2024年"]
    rows = [["营业收入", "1,000", "800"], ["净利润", "100", "80"]]
    facts = parse_tabular(
        headers, rows, "测试公司", 2025, StatementType.INCOME_STATEMENT, "r1", unit="万元"
    )
    assert len(facts) == 4
    rev_2025 = next(f for f in facts if f.standard_item == "revenue" and f.period == "2025")
    assert rev_2025.value == 1000 * 1e4  # 万元 → 元
    assert rev_2025.raw_value == 1000.0
    assert rev_2025.unit == "万元"


def test_parse_tabular_skips_meta_rows():
    headers = ["项目", "2025年"]
    rows = [["项目", "金额"], ["营业收入", "100"]]
    facts = parse_tabular(headers, rows, "c", 2025, StatementType.INCOME_STATEMENT, "r1")
    # 「项目」行应被跳过，只剩营业收入
    assert len(facts) == 1
    assert facts[0].standard_item == "revenue"


def test_is_value_column_empty_header_is_false():
    """空列头不是数值列（招股书表格竖线过度分割产生的空分隔列）。"""
    assert _is_value_column("") is False
    assert _is_value_column(None) is True  # 列不存在才「保守当数值列」
    assert _is_value_column("附注 20") is False  # 附注前缀（text 策略下表头被污染）
    assert _is_value_column("2025年12月31日") is True
    assert _is_value_column("123") is True


def test_detect_item_col_shifted_by_empty_leading_col():
    """科目列识别：招股书第一列为空、科目在第二列；年报第一列即科目。"""
    assert _detect_item_col(["项目", "2025年", "2024年"]) == 0
    assert _detect_item_col(["", "资产", "", "附注", "2025年12月31日"]) == 1
    assert _detect_item_col([]) == 0  # 兜底


def test_parse_tabular_shifted_item_col_and_period_fallback():
    """招股书风格：空首列 + 科目在第 2 列 + 表头年份被 text 策略拆乱（「25年12月31日 20」）。

    应正确识别科目列、排除附注列、并按下标顺序倒推期间（最近期在左）。
    """
    headers = ["", "资产", "附注 20", "25年12月31日 20", "24年12月31日 20", "23年12月31日"]
    rows = [["", "货币资金", "(五)1", "51,990", "42,698", "31,373"]]
    facts = parse_tabular(
        headers, rows, "长鑫科技", 2025, StatementType.BALANCE_SHEET, "r1", unit="元"
    )
    money = [f for f in facts if f.standard_item == "cash_and_equivalents"]
    assert len(money) == 3
    by_period = {f.period: f.value for f in money}
    assert by_period == {"2025": 51990.0, "2024": 42698.0, "2023": 31373.0}
