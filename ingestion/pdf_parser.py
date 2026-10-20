"""pdf_parser —— 原生文本 PDF 解析（pdfplumber）。

恢复页面文本 + 表格，产出 PageContent（供 chunker 结构切块）+ 尽力而为的 FinancialFact。
视觉解析（Qwen-VL）兜底是 P2，不在阶段 1 范围。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pdfplumber

from domain import FinancialFact, Provenance, ReportDoc, StatementType
from finance.taxonomy import RULES_VERSION
from ingestion.excel_parser import build_report_doc, parse_tabular


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


def _is_text(cell) -> bool:
    """单元格是否为文字（用于判断第一列是否是科目名）。"""
    if cell is None:
        return False
    s = str(cell).strip()
    if not s:
        return False
    return s.isalpha() or any("一" <= ch <= "鿿" for ch in s)


def _table_to_facts(
    table: list,
    company_name: str,
    fiscal_year: int,
    statement_type: StatementType,
    report_id: str,
    source_file: str | None,
) -> list[FinancialFact]:
    """把单个 PDF 表格尽力转为财务事实（找表头行后复用 parse_tabular）。"""
    if not table or len(table) < 2:
        return []

    header_idx: int | None = None
    # 先在前 3 行找明确的表头标记行
    for i in range(min(3, len(table))):
        row = table[i]
        if not row:
            continue
        first = str(row[0] or "").strip()
        if first in _HEADER_MARKERS:
            header_idx = i
            break
    # 退而求其次：第一行第一列是文字就当表头
    if header_idx is None and _is_text(table[0][0] if table[0] else None):
        header_idx = 0
    if header_idx is None:
        return []

    headers = table[header_idx]
    rows = table[header_idx + 1 :]
    return parse_tabular(
        headers,
        rows,
        company_name,
        fiscal_year,
        statement_type,
        report_id,
        source_file=source_file,
    )


def parse_pdf(
    path: str,
    company_name: str,
    fiscal_year: int,
    statement_type: StatementType,
    currency: str = "CNY",
) -> PdfParseResult:
    """解析原生文本 PDF，逐页提取文本与表格。"""
    report_doc = build_report_doc(path, company_name, fiscal_year, statement_type)
    pages: list[PageContent] = []
    facts: list[FinancialFact] = []
    provenance: list[Provenance] = []

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            tables = page.extract_tables() or []
            pages.append(PageContent(page_number=page.page_number, text=text, tables=tables))

            for table in tables:
                fs = _table_to_facts(
                    table, company_name, fiscal_year, statement_type,
                    report_doc.report_id, path,
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
