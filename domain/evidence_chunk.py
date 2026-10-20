"""EvidenceChunk —— 文本证据片段。

对应方案第 6 章「数据模型」第 4 类。带结构元数据（章节路径/页码/父子关系），
支撑 RAG 检索时的元数据过滤（不能只靠相似度）。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ChunkType(str, Enum):
    SECTION = "section"      # 章节标题
    PARAGRAPH = "paragraph"  # 正文段落
    TEXT = "text"            # 通用文本
    TABLE = "table"          # 表格（不进向量库，但保留结构）


class EvidenceChunk(BaseModel):
    chunk_id: str
    report_id: str
    company_name: str
    fiscal_year: int

    # 结构元数据（方案 5.11）
    section_path: str | None = Field(default=None, description="章节路径，如 三、财务报表/资产负债表")
    page_start: int | None = None
    page_end: int | None = None
    parent_chunk_id: str | None = Field(default=None, description="父 chunk（章节）ID")
    chunk_type: ChunkType = ChunkType.TEXT
    source_span: str | None = Field(default=None, description="源文本跨度，如 page 3 para 2")

    text: str = ""
    document_version: int = 1
