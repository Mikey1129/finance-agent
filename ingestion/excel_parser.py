"""excel_parser —— 财报 Excel/CSV 解析。

产出 ReportDoc + FinancialFact + Provenance。
中文财报三大坑（方案第 15 章）：单位换算、负数表示、期间列。

约定输入结构（最常见的财报表格）：第一行表头（第一列=科目名，其余列=各期数值），
后续行为数据行；单位可全局传入，也可从表头或前几行的「单位：xx」标注自动检测。
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field

import pandas as pd

from domain import (
    AccountingStandard,
    DataStatus,
    FinancialFact,
    ParseStatus,
    PeriodType,
    Provenance,
    ReportDoc,
    ReportType,
    StatementType,
)
from finance.taxonomy import RULES_VERSION, convert_to_yuan, map_item, normalize_name


@dataclass
class ParseResult:
    """一次文件解析的产出：报告元数据 + 财务事实 + 来源血缘。"""

    report_doc: ReportDoc
    facts: list[FinancialFact] = field(default_factory=list)
    provenance: list[Provenance] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 数值解析
# ---------------------------------------------------------------------------
_NAN_STRINGS = {"", "nan", "none", "null"}
_NEG_FULLWIDTH = "（）"
_UNIT_FALLBACK = ["万元", "亿元", "千元", "元"]  # 先长后短，避免「万元」被「元」截胡


_FIRST_NUM = re.compile(r"[-+]?(?:\d[\d,]*\.?\d*|\.\d+)")


def _first_number(s: str) -> float | None:
    """从字符串提取第一个数字，处理「A ( B )」并列格式（取 A）。"""
    m = _FIRST_NUM.search(s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_number(raw) -> tuple[float | None, DataStatus]:
    """把单元格原文解析为 (数值, 数据状态)。

    处理：千分位、货币符号、括号负数、全角括号、缺失/未披露/不适用标记。
    """
    if raw is None:
        return None, DataStatus.MISSING
    if isinstance(raw, bool):
        return None, DataStatus.PARSE_ERROR
    if isinstance(raw, (int, float)):
        v = float(raw)
        return (0.0, DataStatus.ZERO) if v == 0 else (v, DataStatus.PRESENT)

    s = str(raw).strip()
    if s.lower() in _NAN_STRINGS:
        return None, DataStatus.MISSING

    # 未披露 / 不适用标记
    if "不适用" in s or s.lower() in {"n/a", "na", "n.a.", "n.a"}:
        return None, DataStatus.NOT_APPLICABLE
    if s in {"--", "—", "-", "－", "无", "未披露", "null"}:
        return None, DataStatus.NOT_DISCLOSED

    t = s.replace(",", "").replace("$", "").replace("¥", "").replace("￥", "")
    t = t.replace(" ", "").replace("　", "")

    # 括号表示负数
    neg = False
    if t.startswith("(") and t.endswith(")"):
        t, neg = t[1:-1], True
    elif t.startswith("（") and t.endswith("）"):
        t, neg = t[1:-1], True
    if t.startswith("-"):
        t, neg = t[1:], True

    try:
        v = float(t)
    except ValueError:
        v = _first_number(s)  # 兜底：并列格式「A ( B )」取第一个数 A
        if v is None:
            return None, DataStatus.PARSE_ERROR
    if neg:
        v = -v
    return (0.0, DataStatus.ZERO) if v == 0 else (v, DataStatus.PRESENT)


# ---------------------------------------------------------------------------
# 单位检测
# ---------------------------------------------------------------------------
def detect_unit(text: str | None) -> str | None:
    """从文本提取单位，如「单位：万元」「（单位：千元）」「in millions」。

    直接在原始文本搜索单位关键词（不用 normalize_name，否则会误删「（单位：千元）」）。
    """
    if not text:
        return None
    s = str(text)
    for u in _UNIT_FALLBACK:
        if u in s:
            return u
    low = s.lower()
    for u in ("millions", "million", "billions", "billion", "thousands", "thousand"):
        if u in low:
            return u
    return None


def _scan_unit_from_rows(rows: list[list]) -> str | None:
    """扫描前几行，找「单位：xx」标注行。"""
    for row in rows[:5]:
        if not row:
            continue
        joined = " ".join(str(c) for c in row)
        if "单位" in joined:
            return detect_unit(joined)
    return None


# ---------------------------------------------------------------------------
# 期间提取
# ---------------------------------------------------------------------------
def extract_period(header: str | None) -> str | None:
    """从列头提取期间，如「2025年」「2024-12-31」→「2025」。"""
    if header is None:
        return None
    m = re.search(r"(20\d{2}|19\d{2})", str(header))
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# 表格解析
# ---------------------------------------------------------------------------
_NON_VALUE_HEADERS = {"附注", "备注", "注释", "附註", "notes", "note"}


def _is_value_column(header) -> bool:
    """判断列是否为数值/期间列（跳过附注/备注等文字列）。"""
    if header is None:
        return True  # 无列头，保守当数值列
    h = str(header).strip()
    if not h:
        return True
    if h.lower() in _NON_VALUE_HEADERS:
        return False
    if extract_period(h) is not None:
        return True
    return any(ch.isdigit() for ch in h)


def parse_tabular(
    headers: list,
    rows: list[list],
    company_name: str,
    fiscal_year: int,
    statement_type: StatementType,
    report_id: str,
    currency: str = "CNY",
    unit: str | None = None,
    period_map: dict[int, str] | None = None,
    period_type: PeriodType | None = None,
    source_file: str | None = None,
) -> list[FinancialFact]:
    """核心表格解析：第一列=科目名，其余列=各期数值。

    period_map: {列索引 -> 期间}；缺省时从表头自动提取年份。
    """
    facts: list[FinancialFact] = []
    n = len(headers)
    value_cols = [
        c for c in range(1, n) if _is_value_column(headers[c] if c < len(headers) else None)
    ]
    for r_idx, row in enumerate(rows):
        if not row:
            continue
        raw_item = str(row[0]).strip()
        if not raw_item or raw_item in {"项目", "科目", "指标"}:
            continue
        std = map_item(raw_item)
        for c_idx in value_cols:
            cell = row[c_idx] if c_idx < len(row) else None
            value, status = parse_number(cell)

            col_unit = unit if unit is not None else detect_unit(str(headers[c_idx]))
            std_value = convert_to_yuan(value, col_unit) if value is not None else None

            period = period_map.get(c_idx) if period_map else extract_period(headers[c_idx])
            fact_id = f"{report_id}:{c_idx}:{r_idx}"
            facts.append(
                FinancialFact(
                    fact_id=fact_id,
                    report_id=report_id,
                    company_name=company_name,
                    fiscal_year=fiscal_year,
                    raw_item=raw_item,
                    standard_item=std,
                    value=std_value,
                    raw_value=value,
                    unit=col_unit,
                    currency=currency,
                    period=period,
                    period_type=period_type or PeriodType.ANNUAL,
                    statement_type=statement_type,
                    data_status=status,
                    mapping_rule_version=RULES_VERSION,
                )
            )
    return facts


# ---------------------------------------------------------------------------
# 文件入口
# ---------------------------------------------------------------------------
def file_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def build_report_doc(
    path: str, company_name: str, fiscal_year: int, statement_type: StatementType
) -> ReportDoc:
    return ReportDoc(
        report_id=f"{company_name}-{fiscal_year}-{statement_type.value}",
        company_name=company_name,
        fiscal_year=fiscal_year,
        accounting_standard=AccountingStandard.CAS,
        report_type=ReportType.ANNUAL,
        source_file=path,
        file_hash=file_hash(path),
        parse_status=ParseStatus.PARSED,
    )


def _make_result(
    report_doc: ReportDoc,
    facts: list[FinancialFact],
    source_file: str | None,
) -> ParseResult:
    """为每条事实生成来源血缘记录。"""
    provs: list[Provenance] = []
    for f in facts:
        p = Provenance(
            provenance_id=f"prov-{f.fact_id}",
            report_id=report_doc.report_id,
            source_file=source_file,
            extraction_method="excel_cell" if source_file and source_file.endswith((".xlsx", ".xls")) else "csv_cell",
            mapping_rule_version=RULES_VERSION,
            transform_steps=([f"单位换算:{f.unit}→元"] if f.unit and f.unit != "元" else []),
        )
        f.provenance_id = p.provenance_id
        provs.append(p)
    return ParseResult(report_doc=report_doc, facts=facts, provenance=provs)


def parse_csv(
    path: str,
    company_name: str,
    fiscal_year: int,
    statement_type: StatementType,
    unit: str | None = None,
    period_map: dict[int, str] | None = None,
    encoding: str = "utf-8-sig",
) -> ParseResult:
    """解析财报 CSV 文件。"""
    with open(path, encoding=encoding, newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return _make_result(build_report_doc(path, company_name, fiscal_year, statement_type), [], path)
    headers = rows[0]
    data = rows[1:]
    if unit is None:
        unit = _scan_unit_from_rows(rows)
        if unit is not None:
            data = [r for r in data if "单位" not in " ".join(str(c) for c in r)]
    report_doc = build_report_doc(path, company_name, fiscal_year, statement_type)
    facts = parse_tabular(
        headers, data, company_name, fiscal_year, statement_type, report_doc.report_id,
        unit=unit, period_map=period_map, source_file=path,
    )
    return _make_result(report_doc, facts, path)


def parse_excel(
    path: str,
    company_name: str,
    fiscal_year: int,
    statement_type: StatementType,
    sheet_name: int | str = 0,
    unit: str | None = None,
    period_map: dict[int, str] | None = None,
) -> ParseResult:
    """解析财报 Excel 文件（默认第一个 sheet，无表头，第一行即表头）。"""
    df = pd.read_excel(path, sheet_name=sheet_name, header=None)
    rows = df.values.tolist()
    if not rows:
        return _make_result(build_report_doc(path, company_name, fiscal_year, statement_type), [], path)
    headers = rows[0]
    data = rows[1:]
    if unit is None:
        unit = _scan_unit_from_rows(rows)
        if unit is not None:
            data = [r for r in data if "单位" not in " ".join(str(c) for c in r)]
    report_doc = build_report_doc(path, company_name, fiscal_year, statement_type)
    facts = parse_tabular(
        headers, data, company_name, fiscal_year, statement_type, report_doc.report_id,
        unit=unit, period_map=period_map, source_file=path,
    )
    return _make_result(report_doc, facts, path)
