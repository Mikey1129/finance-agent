"""BGE embedding + 混合召回（RRF 融合）测试。"""

import numpy as np

from domain import EvidenceChunk
from retrieval.embedding import BGEEmbedder
from retrieval.retriever import Retriever, _rrf_fuse


def test_bge_embedder_injectable_encode():
    emb = BGEEmbedder(encode_fn=lambda texts: np.zeros((len(texts), 3), dtype="float32"))
    v = emb.encode(["a", "b"])
    assert v.shape == (2, 3)


def test_bge_embedder_encode_fn_none_returns_none():
    # 注入 encode_fn 返回 None（模拟编码失败）→ 降级返回 None，不崩溃
    emb = BGEEmbedder(encode_fn=lambda texts: None)
    assert emb.encode(["a"]) is None


def test_rrf_fuse_combines_rankings():
    # a=[0,1,2] b=[2,0,1]：0 在两路都靠前，融合后应排第一
    fused = _rrf_fuse([0, 1, 2], [2, 0, 1], top_k=3)
    assert fused[0] == 0


def test_hybrid_retrieval_uses_vector_signal():
    # c2「回款周期拉长」与 query「应收账款周转」词法不匹配（BM25 得 0 分），
    # 但语义相似（向量高相似），混合召回应把它捞回 top_k。
    chunks = [
        EvidenceChunk(chunk_id="c1", report_id="r", company_name="公牛", fiscal_year=2024,
                      text="应收账款期末余额增加"),
        EvidenceChunk(chunk_id="c2", report_id="r", company_name="公牛", fiscal_year=2024,
                      text="客户回款周期拉长"),
        EvidenceChunk(chunk_id="c3", report_id="r", company_name="公牛", fiscal_year=2024,
                      text="货币资金充足"),
    ]
    doc_vecs = np.array([[0.5, 0.0], [1.0, 0.0], [0.0, 0.5]], dtype="float32")
    query_vec = np.array([1.0, 0.0], dtype="float32")
    emb = BGEEmbedder(
        encode_fn=lambda texts: np.array([query_vec]) if len(texts) == 1 else doc_vecs
    )
    r = Retriever(chunks, embedder=emb)
    top = r.retrieve("应收账款周转", company_name="公牛", fiscal_year=2024, top_k=2)
    ids = [c.chunk_id for c in top]
    assert "c2" in ids


def test_retriever_without_embedder_is_pure_bm25():
    chunks = [
        EvidenceChunk(chunk_id="c1", report_id="r", company_name="公牛", fiscal_year=2024,
                      text="应收账款期末余额"),
        EvidenceChunk(chunk_id="c2", report_id="r", company_name="公牛", fiscal_year=2024,
                      text="完全无关的内容"),
    ]
    r = Retriever(chunks)  # 无 embedder → 纯 BM25
    top = r.retrieve("应收账款", company_name="公牛", fiscal_year=2024)
    assert top[0].chunk_id == "c1"
