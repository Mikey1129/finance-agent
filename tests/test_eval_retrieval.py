"""RAG 检索评测模块测试：指标函数正确性 + gold 匹配逻辑 + 评测集完整性。"""

from domain import EvidenceChunk
from evaluation.eval_retrieval import (
    BUILTIN_QUERIES,
    _norm,
    gold_chunk_ids,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


def test_recall_and_precision():
    ranked = ["a", "b", "c"]
    gold = {"a", "c"}
    assert recall_at_k(ranked, gold, k=3) == 1.0
    assert recall_at_k(ranked, gold, k=2) == 0.5      # 前 2 只命中 a
    assert precision_at_k(ranked, gold, k=3) == 2 / 3


def test_recall_empty_gold_returns_zero():
    assert recall_at_k(["a"], set(), k=5) == 0.0


def test_mrr_first_gold_rank():
    assert mrr(["x", "g", "y"], {"g"}) == 0.5          # 排名 2 → 1/2
    assert mrr(["x", "y"], {"g"}) == 0.0               # 未命中


def test_ndcg_perfect_and_partial():
    ranked = ["g1", "g2", "x", "y"]
    gold = {"g1", "g2"}
    assert ndcg_at_k(ranked, gold, k=2) == 1.0         # 完美排序
    assert 0.0 < ndcg_at_k(["x", "g1"], gold, k=2) < 1.0  # 部分命中


def test_gold_chunk_ids_normalizes_whitespace():
    chunks = [
        EvidenceChunk(chunk_id="c1", report_id="r", company_name="公牛", fiscal_year=2024,
                      text="新能源业务收入7.77\n亿元，同比增长104.75%"),
        EvidenceChunk(chunk_id="c2", report_id="r", company_name="公牛", fiscal_year=2024,
                      text="其他无关内容"),
    ]
    # 短语含换行/空格时也能稳健匹配
    assert gold_chunk_ids(chunks, ["新能源业务收入7.77亿元"]) == {"c1"}


def test_norm_strips_all_whitespace():
    assert _norm(" 营收 168.31\n亿元 ") == "营收168.31亿元"


def test_builtin_queries_well_formed():
    assert len(BUILTIN_QUERIES) >= 8
    for q in BUILTIN_QUERIES:
        assert q.qid and q.query
        assert q.gold_phrases, f"{q.qid} 缺 gold 锚点"
