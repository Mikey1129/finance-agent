"""chunker —— 结构感知切块。

不按固定字符数切块（方案 5.11）：先按段落切分，识别章节标题建立父子关系，
保留章节路径与页码等来源元数据。过长段落二次分割（后续按评测集调长度）。
"""

from __future__ import annotations

import re

from domain import ChunkType, EvidenceChunk

# 章节标题模式：一、二、三、 / 1. / 1.1 / 第一章 / 第一节
_SECTION_PATTERNS = [
    re.compile(r"^[一二三四五六七八九十百]+[、.．]"),
    re.compile(r"^\d+(\.\d+)*[、.．\s]"),
    re.compile(r"^第[一二三四五六七八九十百]+[章节部分]"),
]

# 常见财报章节标题关键词（用于补充识别）
_SECTION_KEYWORDS = (
    "资产负债表", "利润表", "现金流量表", "财务报表", "重要提示",
    "公司简介", "经营情况", "管理层讨论", "董事", "监事", "审计报告",
)


def is_section_heading(text: str) -> bool:
    """判断一段文本是否为章节标题。"""
    s = text.strip()
    if not s or len(s) > 50:  # 标题不会太长
        return False
    if any(p.match(s) for p in _SECTION_PATTERNS):
        return True
    # 短文本且以章节关键词结尾，视为标题（如「三、资产负债表」已命中数字模式）
    return False


def _split_paragraphs(text: str) -> list[str]:
    """按空行切段落。"""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


# 句子边界：句末标点或换行之后、非空白之前。用于长段落二次分割。
_SENT_BOUNDARY_RE = re.compile(r"(?<=[。！？；\n])(?=\S)")


def _split_long_text(text: str, max_len: int) -> list[str]:
    """把长文本按句子边界切分，贪心合并到不超过 max_len。

    PDF extract_text 每行以换行结尾（无空行分隔段落），整页文本常被当成一个超长
    「段落」。这里按句末标点/换行二次分割（方案 5.11「过长段落再二次分割」），
    单句仍超长时（无标点长串）兜底硬切，保证 chunk 粒度可用于检索。
    """
    if len(text) <= max_len:
        return [text]
    parts = [p for p in _SENT_BOUNDARY_RE.split(text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in parts:
        if buf and len(buf) + len(p) > max_len:
            chunks.append(buf)
            buf = p
        else:
            buf += p
    if buf.strip():
        chunks.append(buf)
    final: list[str] = []
    for c in chunks:
        while len(c) > max_len:
            final.append(c[:max_len])
            c = c[max_len:]
        final.append(c)
    return [c for c in final if c.strip()]


def chunk_text(
    text: str,
    company_name: str,
    fiscal_year: int,
    report_id: str,
    page_start: int | None = None,
    page_end: int | None = None,
    id_seed: str | None = None,
    max_chunk_len: int = 400,
) -> list[EvidenceChunk]:
    """把纯文本切成 EvidenceChunk 列表（保留章节父子关系）。

    先按空行切段落、识别章节标题建立父子关系；过长段落按句子二次分割。
    id_seed 用于跨页/跨文档区分 chunk_id（chunk_pages 传页码），保证全局唯一——
    否则 PDF 每页都从 0 编号，不同页 chunk_id 会重复，导致检索去重误删、引用悬空。
    """
    chunks: list[EvidenceChunk] = []
    paragraphs = _split_paragraphs(text)
    current_section_id: str | None = None
    current_section: str | None = None
    seq = 0  # 全文档递增序号（而非段落索引），长段落分割后 id 仍唯一

    def cid(kind: str) -> str:
        if id_seed:
            return f"{report_id}:{id_seed}:{kind}:{seq}"
        return f"{report_id}:{kind}:{seq}"

    for idx, para in enumerate(paragraphs):
        if is_section_heading(para):
            current_section = para
            current_section_id = cid("sec")
            chunks.append(
                EvidenceChunk(
                    chunk_id=current_section_id,
                    report_id=report_id,
                    company_name=company_name,
                    fiscal_year=fiscal_year,
                    section_path=current_section,
                    page_start=page_start,
                    page_end=page_end,
                    chunk_type=ChunkType.SECTION,
                    source_span=f"para {idx}",
                    text=para,
                )
            )
            seq += 1
            continue

        segs = _split_long_text(para, max_chunk_len)
        for j, seg in enumerate(segs):
            chunks.append(
                EvidenceChunk(
                    chunk_id=cid("chunk"),
                    report_id=report_id,
                    company_name=company_name,
                    fiscal_year=fiscal_year,
                    section_path=current_section,
                    page_start=page_start,
                    page_end=page_end,
                    parent_chunk_id=current_section_id,
                    chunk_type=ChunkType.PARAGRAPH,
                    source_span=f"para {idx}" if len(segs) == 1 else f"para {idx}#{j}",
                    text=seg,
                )
            )
            seq += 1
    return chunks


def chunk_pages(
    pages,
    company_name: str,
    fiscal_year: int,
    report_id: str,
    max_chunk_len: int = 400,
) -> list[EvidenceChunk]:
    """从 pdf_parser 的 PageContent 列表切块（带页码，id_seed=页码保证跨页唯一）。"""
    chunks: list[EvidenceChunk] = []
    for page in pages:
        chunks.extend(
            chunk_text(
                page.text,
                company_name,
                fiscal_year,
                report_id,
                page_start=page.page_number,
                page_end=page.page_number,
                id_seed=f"p{page.page_number}",
                max_chunk_len=max_chunk_len,
            )
        )
    return chunks
