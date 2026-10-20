"""缓存 + 历史摘要压缩测试（阶段 5）。"""

from __future__ import annotations

from agent.summary import HistorySummarizer
from domain import EvidenceChunk
from retrieval.cache import RetrievalCache
from retrieval.retriever import Retriever


def _chunks():
    return [
        EvidenceChunk(
            chunk_id=f"c{i}", report_id="r", company_name="公牛集团", fiscal_year=2024,
            text=f"第 {i} 段 营业收入 净利润 现金流 {i}",
        )
        for i in range(5)
    ]


# -- RetrievalCache ---------------------------------------------------------
def test_cache_put_get_hit():
    c = RetrievalCache()
    c.put("营收", "公牛集团", 2024, None, 5, result=["a", "b"])
    assert c.get("营收", "公牛集团", 2024, None, 5) == ["a", "b"]


def test_cache_miss():
    c = RetrievalCache()
    assert c.get("x") is None


def test_cache_key_includes_filters():
    c = RetrievalCache()
    c.put("营收", "公牛集团", 2024, None, 5, result=["a"])
    # 不同公司 / 年度 / top_k 不应命中
    assert c.get("营收", "茅台股份", 2024, None, 5) is None
    assert c.get("营收", "公牛集团", 2023, None, 5) is None
    assert c.get("营收", "公牛集团", 2024, None, 3) is None


def test_cache_ttl_zero_expires_immediately():
    c = RetrievalCache(ttl_seconds=0)
    c.put("x", result=["a"])
    assert c.get("x") is None


def test_cache_returns_shallow_copy():
    c = RetrievalCache()
    c.put("x", result=["a"])
    got = c.get("x")
    got.append("b")
    assert c.get("x") == ["a"]  # 缓存未被调用方污染


def test_cache_evicts_oldest_when_full():
    c = RetrievalCache(max_size=2)
    c.put("a", result=[1])
    c.put("b", result=[2])
    c.put("c", result=[3])  # 满，淘汰最早写入的 a
    assert c.get("a") is None
    assert c.get("b") == [2]
    assert c.get("c") == [3]


def test_cache_clear():
    c = RetrievalCache()
    c.put("x", result=["a"])
    c.clear()
    assert len(c) == 0 and c.get("x") is None


# -- Retriever + cache ------------------------------------------------------
def test_retriever_uses_cache():
    cache = RetrievalCache()
    r = Retriever(_chunks(), cache=cache)
    first = r.retrieve("营业收入", company_name="公牛集团", fiscal_year=2024, top_k=3)
    assert first  # 首次有结果并写入缓存
    assert len(cache) == 1
    second = r.retrieve("营业收入", company_name="公牛集团", fiscal_year=2024, top_k=3)
    assert [c.chunk_id for c in second] == [c.chunk_id for c in first]  # 缓存命中一致


def test_retriever_no_cache_by_default():
    r = Retriever(_chunks())
    r.retrieve("营业收入", top_k=3)
    assert r.cache is None


# -- HistorySummarizer ------------------------------------------------------
def test_summarizer_splits_recent_and_older():
    s = HistorySummarizer(recent_turns=2)
    history = [
        "公牛集团2023年资产总计是多少？",
        "公牛集团2024年净利润是多少？",
        "茅台股份2024年毛利率是多少？",
    ]
    r = s.summarize(history)
    assert r.recent == history[-2:]
    assert "公牛集团" in r.summary  # 更早历史中的公司被提取进摘要
    assert "2023" in r.summary


def test_summarizer_extracts_subject_item():
    s = HistorySummarizer(recent_turns=1)
    r = s.summarize(["公牛集团2023年资产总计是多少？", "继续分析"])
    assert "资产总计" in r.summary


def test_summarizer_empty_history():
    r = HistorySummarizer().summarize([])
    assert r.recent == [] and r.summary == ""


def test_summarizer_all_within_window_no_summary():
    s = HistorySummarizer(recent_turns=5)
    r = s.summarize(["a", "b"])
    assert r.recent == ["a", "b"]
    assert r.summary == ""
