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

# 报表标题 -> (报表类型, 合并范围)。三大主表锚点 + 权益变动表（非主表，遇到即结束主表正文）。
_REPORT_TITLES = (
    ("合并资产负债表", StatementType.BALANCE_SHEET, "consolidated"),
    ("母公司资产负债表", StatementType.BALANCE_SHEET, "parent_only"),
    ("合并利润表", StatementType.INCOME_STATEMENT, "consolidated"),
    ("母公司利润表", StatementType.INCOME_STATEMENT, "parent_only"),
    ("合并现金流量表", StatementType.CASH_FLOW, "consolidated"),
    ("母公司现金流量表", StatementType.CASH_FLOW, "parent_only"),
)

# 非主表报表标题（股东权益变动表等），命中后不再产出主表事实
_NON_TARGET_TITLES = ("所有者权益变动表", "股东权益变动表")


def _find_header_idx(table: list) -> int | None:
    """定位表头行：前 3 行里含「项目/科目」等标记、或列头带年份、或含附注列标题。

    text 策略下表头年份可能被拆乱（「25年12月31日 20」→ extract_period 失败），
    但附注列标题（「附注」「附注 20」）仍在；数据行的附注是「(五)1」编号，据此区分。
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
        if any(str(c or "").strip().startswith(("附注", "备注", "附註")) for c in row[1:]):
            return i
    return None


def _is_title(text: str, title: str) -> bool:
    """text 是否为 title 的标题：精确相等，或仅带 ≤4 字短前缀（合并/母公司）。

    避免子串匹配把正文句子里的「在合并资产负债表中…」误当报表标题锚点
    （招股书/审计报告正文常见，extract_words 会把整段无空格中文当一个词）。
    """
    t = (text or "").strip()
    if not t:
        return False
    if t == title:
        return True
    return t.endswith(title) and len(t) - len(title) <= 4


def _match_title(text: str) -> tuple[StatementType | None, str | None] | None:
    """从词文本识别报表标题。

    命中主表标题返回 (statement_type, scope)；命中非主表标题返回 (None, None)；否则 None。
    """
    for title, st, scope in _REPORT_TITLES:
        if _is_title(text, title):
            return st, scope
    for title in _NON_TARGET_TITLES:
        if _is_title(text, title):
            return None, None
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
    row_offset: int = 0,
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
        row_offset=row_offset,
    )


def _has_excessive_empty_cols(table) -> bool:
    """判断表格数值列是否被竖线过度分割（招股书/审计报告数字对齐竖线）。

    特征：表头含 ≥2 个带年份的列（「2025年12月31日」「2024年12月31日」…），
    但年份列之间夹杂空列（不相邻）。lines 策略会把每个数字对齐竖线拆成一列，
    产生「年份列 + 空列 + 年份列」的锯齿；简单年报表头年份列相邻，权益变动表/
    附注表每页仅一个年份列或无年份列，均不会误判。
    """
    try:
        rows = table.extract()
    except Exception:
        return False
    if not rows:
        return False
    header = rows[0]
    year_cols = [c for c, h in enumerate(header) if extract_period(h) is not None]
    if len(year_cols) < 2:
        return False
    gaps = [year_cols[i + 1] - year_cols[i] for i in range(len(year_cols) - 1)]
    return any(g > 1 for g in gaps)


def parse_pdf(
    path: str,
    company_name: str,
    fiscal_year: int,
    statement_type: StatementType,
    currency: str = "CNY",
) -> PdfParseResult:
    """解析原生文本 PDF，按报表标题定位并只提取目标报表的表格。

    用「标题词 y 坐标 + 表格 bbox」的事件流精确归属：标题在上、表格在下 → 表格属新报表；
    标题在下、表格在上 → 表格属上一报表（标题跨页到下一页的常见排版）。
    跨页继承：同一报表内维护表头/单位，续页表复用上一表头。
    无任何主表标题时回退到「全部按传入 statement_type 解析」（旧行为）。
    """
    report_doc = build_report_doc(path, company_name, fiscal_year, statement_type)
    pages: list[PageContent] = []
    facts: list[FinancialFact] = []
    provenance: list[Provenance] = []

    with pdfplumber.open(path) as pdf:
        page_list = list(pdf.pages)
        # 预扫：是否存在主表标题锚点（决定是否按标题筛选）
        has_anchor = any(
            any(_match_title(w["text"]) is not None for w in page.extract_words())
            for page in page_list
        )

        current_statement: StatementType | None = None if has_anchor else statement_type
        current_scope: str = "consolidated"
        last_headers: list | None = None
        current_unit: str | None = None
        current_row_offset: int = 0  # 跨页续表累计行偏移（新报表标题处归零）

        for page in page_list:
            text = page.extract_text() or ""
            page_unit = detect_unit(text)

            # 事件流：标题词 + 表格，按 y 从顶到底排序
            events: list[tuple[float, str, object]] = []
            words = page.extract_words()
            # 续页标题「合并资产负债表 - 续」：同一行带「续」标记 → 非新报表。
            # 据此识别续页（- 续 / - 续表），避免把续页标题误当新报表标题而重置跨页状态。
            continuation_tops = {
                round(w["top"], 1)
                for w in words
                if (w.get("text") or "").strip() in ("续", "-续", "－续", "续表")
            }
            for word in words:
                m = _match_title(word["text"])
                if m is not None:
                    is_cont = round(word["top"], 1) in continuation_tops
                    events.append((word["top"], "title", (*m, is_cont)))
            table_objs = page.find_tables()
            # 招股书/审计报告数值列常带数字对齐竖线，lines 策略会把一列过度分割成
            # 多列（表头空列占比过半、值列错位）。检测到过度分割时改用 text 策略
            # 重提取该页（文本对齐，不受竖线干扰）；简单年报保持 lines 不变。
            if any(_has_excessive_empty_cols(t) for t in table_objs):
                table_objs = page.find_tables(
                    table_settings={"vertical_strategy": "text", "horizontal_strategy": "text"}
                )
            for t in table_objs:
                events.append((t.bbox[1], "table", t))
            events.sort(key=lambda e: e[0])

            pages.append(
                PageContent(
                    page_number=page.page_number,
                    text=text,
                    tables=[t.extract() for t in table_objs],
                )
            )

            for _, kind, payload in events:
                if kind == "title":
                    st, scope, is_cont = payload
                    current_statement = st
                    if scope is not None:
                        current_scope = scope
                        if not is_cont:
                            # 新报表开始：重置表头/单位/行偏移继承（标题下方才是新报表正文）。
                            # 续页标题（「合并资产负债表 - 续」）不重置：跨页行偏移必须延续，
                            # 否则两栏资产负债表的负债侧会与资产侧同位置撞 fact_id 主键。
                            last_headers = None
                            current_unit = None
                            current_row_offset = 0
                    continue

                if current_statement != statement_type:
                    continue  # 当前表格不属于目标报表，跳过

                rows = payload.extract()
                header_idx = _find_header_idx(rows)
                if header_idx is not None:
                    last_headers = rows[header_idx]
                    data_rows = rows[header_idx + 1 :]
                    if page_unit is not None:
                        current_unit = page_unit
                else:
                    if last_headers is None:
                        continue  # 尚未出现表头，无法确定列结构，跳过
                    data_rows = rows

                fs = _table_to_facts(
                    last_headers,
                    data_rows,
                    company_name,
                    fiscal_year,
                    statement_type,
                    report_doc.report_id,
                    path,
                    unit=current_unit,
                    consolidation_scope=current_scope,
                    row_offset=current_row_offset,
                )
                current_row_offset += len(data_rows)
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
