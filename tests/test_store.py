"""store DuckDB 存储与查询测试。"""

from domain import FinancialFact, StatementType
from finance.store import Store


def test_insert_and_query_by_column_name():
    store = Store(":memory:")
    facts = [
        FinancialFact(
            fact_id="f1",
            report_id="r1",
            company_name="测试公司",
            fiscal_year=2025,
            raw_item="营业收入",
            standard_item="revenue",
            value=100.0,
            unit="元",
            statement_type=StatementType.INCOME_STATEMENT,
        )
    ]
    store.insert_financial_facts(facts)
    assert store.count_facts() == 1

    rows = store.query_facts(standard_item="revenue")
    assert len(rows) == 1
    assert rows[0]["fact_id"] == "f1"
    assert rows[0]["value"] == 100.0
    assert rows[0]["statement_type"] == "income_statement"

    vals = store.query_fact_values(standard_item="revenue")
    assert vals == [("f1", 100.0)]
    store.close()
