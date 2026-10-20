"""Checkpointer 测试：保存/加载/敏感信息规则/过期清理/删除/多轮续接。"""

from agent.checkpoint import Checkpointer
from agent.graph import AgentGraph
from domain import FinancialFact, StatementType
from finance.store import Store


def test_save_and_load():
    cp = Checkpointer(":memory:")
    cp.save("t1", {"company_name": "公牛集团", "periods": ["2024"], "intent": "fact_query"})
    st = cp.load("t1")
    assert st["company_name"] == "公牛集团"
    assert st["periods"] == ["2024"]


def test_sensitive_info_stripped():
    cp = Checkpointer(":memory:")
    cp.save("t1", {"company_name": "X", "full_text": "财报全文…很长", "huge_vec": [1] * 1000})
    st = cp.load("t1")
    assert st["company_name"] == "X"
    assert "full_text" not in st
    assert "huge_vec" not in st


def test_load_latest():
    cp = Checkpointer(":memory:")
    cp.save("t1", {"company_name": "A"})
    cp.save("t1", {"company_name": "B"})
    assert cp.load("t1")["company_name"] == "B"


def test_expiry_auto_cleanup():
    cp = Checkpointer(":memory:", ttl_seconds=-1)  # 立即过期
    cp.save("t1", {"company_name": "X"})
    assert cp.load("t1") is None


def test_delete():
    cp = Checkpointer(":memory:")
    cp.save("t1", {"company_name": "X"})
    assert cp.delete("t1")
    assert cp.load("t1") is None
    assert not cp.delete("t1")


def test_list_threads():
    cp = Checkpointer(":memory:")
    cp.save("t1", {"company_name": "A"})
    cp.save("t2", {"company_name": "B"})
    assert set(cp.list_threads()) == {"t1", "t2"}


def _store():
    store = Store(":memory:")
    store.insert_financial_facts(
        [
            FinancialFact(
                fact_id=f"revenue-{y}", report_id="r1", company_name="测试公司",
                fiscal_year=y, raw_item="revenue", standard_item="revenue", value=v,
                unit="元", statement_type=StatementType.INCOME_STATEMENT,
                period=str(y), consolidation_scope="consolidated",
            )
            for y, v in ((2023, 1600.0), (2024, 2000.0))
        ]
    )
    return store


def test_multiturn_continuation():
    cp = Checkpointer(":memory:")
    g = AgentGraph(_store(), checkpointer=cp)

    out1 = g.run("2024 年营收多少", company_name="测试公司", thread_id="t1")
    assert out1["company_name"] == "测试公司"

    # 第二轮不带公司名，应从 checkpoint 继承
    out2 = g.run("营收是多少", thread_id="t1")
    assert out2["company_name"] == "测试公司"
    assert out2["status"] == "success"
