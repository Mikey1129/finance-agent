"""chunker 结构切块测试。"""

from domain import ChunkType
from retrieval.chunker import chunk_text, is_section_heading


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
