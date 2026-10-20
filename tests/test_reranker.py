"""Reranker 候选重排测试（方案 5.11 ④「重排序」）。"""

from domain import EvidenceChunk
from retrieval.reranker import DashScopeReranker
from retrieval.retriever import Retriever


def test_dashscope_reranker_injectable_rank_fn():
    rr = DashScopeReranker(rank_fn=lambda q, docs: [0.1, 0.9])
    assert rr.rank("q", ["a", "b"]) == [0.1, 0.9]


def test_dashscope_reranker_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    rr = DashScopeReranker(api_key=None)  # 无 key、无 rank_fn → 降级 None
    assert rr.rank("q", ["a", "b"]) is None


def test_dashscope_reranker_empty_docs_returns_empty():
    rr = DashScopeReranker()
    assert rr.rank("q", []) == []


def _chunks():
    return [
        EvidenceChunk(chunk_id="c1", report_id="r", company_name="公牛", fiscal_year=2024,
                      text="应收账款期末余额为 12.34 亿元"),
        EvidenceChunk(chunk_id="c2", report_id="r", company_name="公牛", fiscal_year=2024,
                      text="客户回款周期显著拉长"),
        EvidenceChunk(chunk_id="c3", report_id="r", company_name="公牛", fiscal_year=2024,
                      text="货币资金充足"),
    ]


def test_reranker_reranks_candidates():
    # BM25 词法命中「应收账款」把 c1 排前；reranker 按语义把「回款周期拉长」的 c2 提到首位。
    rr = DashScopeReranker(rank_fn=lambda q, docs: [0.9 if "回款周期" in d else 0.1 for d in docs])
    r = Retriever(_chunks(), reranker=rr)
    top = r.retrieve("应收账款为什么回款变慢", company_name="公牛", fiscal_year=2024, top_k=2)
    assert [c.chunk_id for c in top] == ["c2", "c1"]


def test_reranker_none_keeps_bm25_order():
    r = Retriever(_chunks())  # reranker=None → 纯 BM25 顺序
    top = r.retrieve("应收账款为什么回款变慢", company_name="公牛", fiscal_year=2024, top_k=2)
    assert top[0].chunk_id == "c1"


def test_reranker_failure_degrades_to_recall_order():
    rr = DashScopeReranker(rank_fn=lambda q, docs: None)  # 打分失败 → 降级召回原顺序
    r = Retriever(_chunks(), reranker=rr)
    top = r.retrieve("应收账款为什么回款变慢", company_name="公牛", fiscal_year=2024, top_k=2)
    assert top[0].chunk_id == "c1"
