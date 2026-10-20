"""validation 基础校验测试。"""

from domain import DataStatus, FinancialFact, StatementType
from finance.validation import check_balance_sheet, validate_fact


def _fact(fact_id, standard_item, value, statement_type=StatementType.BALANCE_SHEET):
    return FinancialFact(
        fact_id=fact_id,
        report_id="r1",
        company_name="c",
        fiscal_year=2025,
        raw_item=standard_item,
        standard_item=standard_item,
        value=value,
        statement_type=statement_type,
    )


def test_validate_fact_present_but_no_value():
    f = _fact("f1", "revenue", None, StatementType.INCOME_STATEMENT)
    issues = validate_fact(f)
    assert any(i.field == "value" for i in issues)


def test_validate_fact_unmapped_item():
    f = _fact("f1", "某个冷门科目", 100.0)
    issues = validate_fact(f)
    assert any(i.field == "standard_item" for i in issues)


def test_balance_sheet_balanced():
    facts = [
        _fact("a", "total_assets", 200.0),
        _fact("l", "total_liabilities", 120.0),
        _fact("e", "total_equity", 80.0),
    ]
    report = check_balance_sheet(facts)
    assert report.ok()


def test_balance_sheet_unbalanced():
    facts = [
        _fact("a", "total_assets", 200.0),
        _fact("l", "total_liabilities", 100.0),
        _fact("e", "total_equity", 80.0),
    ]
    report = check_balance_sheet(facts)
    assert not report.ok()
    assert report.errors == 1


def test_balance_sheet_missing_item():
    facts = [
        _fact("a", "total_assets", 200.0),
        _fact("l", "total_liabilities", 120.0),
    ]
    report = check_balance_sheet(facts)
    assert report.warnings >= 1  # 缺 total_equity，无法校验，给 warning
