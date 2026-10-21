"""FactRetriever 结构化事实检索测试。"""

from domain import FinancialFact, PeriodType, StatementType
from retrieval.fact_retriever import FactRetriever, extract_item


def _fact(fid, raw, std, value, year="2024", scope="consolidated"):
    return FinancialFact(
        fact_id=fid, report_id="r1", company_name="公牛集团", fiscal_year=2024,
        raw_item=raw, standard_item=std, value=value, unit="元",
        period=year, period_type=PeriodType.ANNUAL, statement_type=StatementType.BALANCE_SHEET,
        consolidation_scope=scope,
    )


def test_extract_item_maps_full_name():
    # 长全称优先命中，不被更短子串抢走
    assert extract_item("2024年经营活动产生的现金流量净额是多少？") == "operating_cash_flow"


def test_extract_item_maps_short_name():
    assert extract_item("2024年末存货余额是多少？") == "inventory"
    assert extract_item("货币资金余额") == "cash_and_equivalents"


def test_extract_item_maps_alias():
    # 口语简称「归母净利润」→ net_profit_parent（归属于母公司股东的净利润）
    assert extract_item("2024年归母净利润是多少？") == "net_profit_parent"


def test_extract_item_unknown_returns_none():
    assert extract_item("公司核心竞争力是什么？") is None


def test_fact_retriever_filters_by_item_and_year():
    facts = [
        _fact("f1", "存货", "inventory", 1_573_672_819.32, year="2024"),
        _fact("f2", "存货", "inventory", 1_421_078_059.44, year="2023"),
        _fact("f3", "存货", "inventory", 333_463_918.47, year="2024", scope="parent_only"),
        _fact("f4", "货币资金", "cash_and_equivalents", 5_020_380_352.96, year="2024"),
    ]
    ret = FactRetriever(facts)
    got = ret.retrieve("2024年末存货余额是多少？", company_name="公牛集团", fiscal_year=2024)
    # 只返回存货、合并口径；当期(2024)排前
    assert all(f.standard_item == "inventory" for f in got)
    assert all(f.consolidation_scope == "consolidated" for f in got)
    assert got[0].value == 1_573_672_819.32  # 当期优先


def test_fact_retriever_unknown_item_returns_empty():
    ret = FactRetriever([_fact("f1", "存货", "inventory", 100.0)])
    assert ret.retrieve("公司核心竞争力是什么？") == []
