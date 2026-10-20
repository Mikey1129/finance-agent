"""chunker 结构切块测试。"""

from domain import ChunkType
from retrieval.chunker import chunk_text, chunk_pages, is_section_heading


def test_is_section_heading():
    assert is_section_heading("三、财务报表")
    assert is_section_heading("1. 公司简介")
    assert is_section_heading("第一章 公司概况")
    assert not is_section_heading("这是一段很长的正文内容，绝对不会被视为章节标题")


def test_chunk_text_section_parent_relationship():
    chunks = chunk_text(
        "三、财务报表\n\n资产负债表是三大报表之一。\n\n货币资金期末余额 100 万元。",
        "测试公司",
        2025,
        "r1",
    )
    types = [c.chunk_type for c in chunks]
    assert types[0] is ChunkType.SECTION
    assert chunks[0].section_path == "三、财务报表"
    # 正文段落挂在章节下
    assert chunks[1].chunk_type is ChunkType.PARAGRAPH
    assert chunks[1].parent_chunk_id == chunks[0].chunk_id
    assert chunks[1].section_path == "三、财务报表"


def test_long_paragraph_split_into_sentences():
    # 无空行分隔的整段长文本（模拟 PDF extract_text），应被按句子二次分割
    text = "。" .join(f"这是第{i}个句子" for i in range(30))
    chunks = chunk_text(text, "测试公司", 2025, "r1", max_chunk_len=80)
    assert len(chunks) > 1
    # 每个 chunk 都不应超过 max_chunk_len（除单句硬切兜底外）
    assert all(len(c.text) <= 80 for c in chunks)


def test_chunk_ids_unique_across_pages():
    # 两页各自无空行长文本，chunk_id 必须跨页唯一（id_seed=页码）
    pages = [type("P", (), {"page_number": 1, "text": "甲" * 500})(),
             type("P", (), {"page_number": 2, "text": "乙" * 500})()]
    chunks = chunk_pages(pages, "测试公司", 2025, "r1", max_chunk_len=100)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids)), "chunk_id 跨页重复"


def test_chunk_pages_short_text_keeps_id_seed():
    pages = [type("P", (), {"page_number": 3, "text": "一句话。", "tables": []})()]
    chunks = chunk_pages(pages, "测试公司", 2025, "r1")
    assert chunks and "p3" in chunks[0].chunk_id
