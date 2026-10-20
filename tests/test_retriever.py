"""retrieval.retriever BM25 检索器测试：排序 / 元数据过滤 / 去重。"""

from domain import EvidenceChunk
from retrieval.retriever import BM25Index, Retriever, tokenize


def _chunks():
    return [
        EvidenceChunk(
            chunk_id="c1", report_id="r1", company_name="公牛集团", fiscal_year=2024,
            section_path="三、财务报表", text="应收账款期末余额大幅增加",
        ),
        EvidenceChunk(
            chunk_id="c2", report_id="r1", company_name="公牛集团", fiscal_year=2024,
            section_path="管理层讨论与分析", text="公司主营业务收入保持稳定增长，应收账款规模可控",
        ),
        EvidenceChunk(
            chunk_id="c3", report_id="r2", company_name="其他公司", fiscal_year=2024,
            section_path="三、财务报表", text="应收账款余额也很多",
        ),
        EvidenceChunk(
            chunk_id="c4", report_id="r1", company_name="公牛集团", fiscal_year=2023,
            section_path="三、财务报表", text="上年应收账款余额",
        ),
    ]


def test_tokenize_mixed():
    tokens = tokenize("营收 revenue 增长")
    assert "revenue" in tokens
    assert "营收" in tokens  # 2-gram
    assert "增长" in tokens


def test_retrieve_rank_by_relevance():
    r = Retriever(_chunks())
    top = r.retrieve("应收账款", company_name="公牛集团", fiscal_year=2024)
    ids = [c.chunk_id for c in top]
    # c1 命中「应收账款」最集中，排最前；c3 被公司过滤；c4 被年度过滤
    assert ids[0] == "c1"
    assert "c3" not in ids
    assert "c4" not in ids


def test_retrieve_section_filter():
    r = Retriever(_chunks())
    top = r.retrieve("应收账款", company_name="公牛集团", section="财务报表")
    ids = [c.chunk_id for c in top]
    # c2 章节为「管理层讨论与分析」，被 section 过滤
    assert "c1" in ids
    assert "c2" not in ids


def test_retrieve_deduplicate():
    chunks = _chunks() + [_chunks()[0]]  # 重复 c1
    r = Retriever(chunks)
    top = r.retrieve("应收账款", company_name="公牛集团")
    ids = [c.chunk_id for c in top]
    assert ids.count("c1") == 1


def test_bm25_idf_downweights_common_term():
    # 高频词（几乎所有文档都含）IDF 低，评分贡献小
    docs = [tokenize("应收账款 增长"), tokenize("应收账款 增长 很快"), tokenize("应收账款 增长 放缓")]
    idx = BM25Index(docs)
    common = idx._idf("应收")  # 应收 出现在每个文档
    rare = idx._idf("很快")     # 仅一个文档
    assert common < rare
