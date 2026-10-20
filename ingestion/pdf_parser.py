"""pdf_parser —— 原生文本 PDF 解析（pdfplumber）。

恢复页面文本 + 表格，产出 PageContent（供 chunker 结构切块）+ 尽力而为的 FinancialFact。
视觉解析（Qwen-VL）兜底是 P2，不在阶段 1 范围。

跨页表格处理：中文财报三大报表常跨多页，续页只重复列数据、不重复表头行
（列头不含年份、也无「项目/科目」标记）。解析时维护「当前报表上下文」
（表头列结构 + 单位 + 合并范围），续页表继承上一表头，保证续页行归到同一期间。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pdfplumber

from domain import FinancialFact, Provenance, ReportDoc, StatementType
from finance.taxonomy import RULES_VERSION
from ingestion.excel_parser import (
    build_report_doc,
    detect_unit,
    extract_period,
    parse_tabular,
)


@dataclass
class PageContent:
    """一页 PDF 的文本 + 表格，供 chunker 做结构切块。"""

    page_number: int
    text: str = ""
    tables: list = field(default_factory=list)


@dataclass
class PdfParseResult:
    report_doc: ReportDoc
    pages: list[PageContent]
    facts: list[FinancialFact]
    provenance: list[Provenance]


_HEADER_MARKERS = {"项目", "科目", "指标", "报表项目", "Items", "Item", "Financial Data"}

# 报表标题 -> 合并范围。标题出现在每张报表首页的页面文本里。
_SCOPE_TITLES = (
    ("母公司资产负债表", "parent_only"),
    ("合并资产负债表", "consolidated"),
)


def _find_header_idx(table: list) -> int | None:
    """定位表头行：前 3 行里含「项目/科目」等标记、或列头带年份的行。

    续页表（首行是数据、列头无年份无标记）返回 None，由调用方继承上一表头。
    """
    if not table:
        return None
    for i in range(min(3, len(table))):
        row = table[i]
        if not row:
            continue
        first = str(row[0] or "").strip()
        if first in _HEADER_MARKERS:
            return i
        if any(extract_period(c) is not None for c in row[1:]):
            return i
    return None


def _detect_scope(text: str) -> str | None:
    """从页面文本识别合并范围；仅在出现明确报表标题时返回。"""
    for title, scope in _SCOPE_TITLES:
        if title in text:
            return scope
    return None


def _table_to_facts(
    headers: list,
    rows: list,
    company_name: str,
    fiscal_year: int,
    statement_type: StatementType,
    report_id: str,
    source_file: str | None,
    unit: str | None = None,
    consolidation_scope: str = "consolidated",
) -> list[FinancialFact]:
    """按给定表头 + 数据行，复用 parse_tabular 转为财务事实。"""
    return parse_tabular(
        headers,
        rows,
        company_name,
        fiscal_year,
        statement_type,
        report_id,
        unit=unit,
        consolidation_scope=consolidation_scope,
        source_file=source_file,
    )


def parse_pdf(
    path: str,
    company_name: str,
    fiscal_year: int,
    statement_type: StatementType,
    currency: str = "CNY",
) -> PdfParseResult:
    """解析原生文本 PDF，逐页提取文本与表格。

    跨页继承：维护「当前表头列结构 / 单位 / 合并范围」三个状态，
    遇到新表头（含年份或标记的行）则重置/更新，续页表则复用上一表头。
    """
    report_doc = build_report_doc(path, company_name, fiscal_year, statement_type)
    pages: list[PageContent] = []
    facts: list[FinancialFact] = []
    provenance: list[Provenance] = []

    last_headers: list | None = None
    current_unit: str | None = None
    current_scope: str = "consolidated"

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            tables = page.extract_tables() or []
            pages.append(PageContent(page_number=page.page_number, text=text, tables=tables))
            page_unit = detect_unit(text)
            page_scope = _detect_scope(text)

            for table in tables:
                header_idx = _find_header_idx(table)
                if header_idx is not None:
                    # 新表头：重建列结构，若本页标注了单位/合并范围则更新
                    last_headers = table[header_idx]
                    rows = table[header_idx + 1 :]
                    if page_unit is not None:
                        current_unit = page_unit
                    if page_scope is not None:
                        current_scope = page_scope
                else:
                    # 续页表：继承上一表头的列结构（含期间）与单位/范围
                    if last_headers is None:
                        continue  # 尚未出现表头，无法确定列结构，跳过
                    rows = table

                fs = _table_to_facts(
                    last_headers,
                    rows,
                    company_name,
                    fiscal_year,
                    statement_type,
                    report_doc.report_id,
                    path,
                    unit=current_unit,
                    consolidation_scope=current_scope,
                )
                for f in fs:
                    f.provenance_id = f"prov-{f.fact_id}"
                    provenance.append(
                        Provenance(
                            provenance_id=f.provenance_id,
                            report_id=report_doc.report_id,
                            source_file=path,
                            page=page.page_number,
                            extraction_method="pdf_table",
                            mapping_rule_version=RULES_VERSION,
                        )
                    )
                    facts.append(f)

    return PdfParseResult(
        report_doc=report_doc, pages=pages, facts=facts, provenance=provenance
    )
