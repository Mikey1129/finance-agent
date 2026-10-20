"""eval_data_extraction —— 评测 A：数据提取（字段级）。

分层评测原则（方案第 9 章）：字段准确率 99% ≠ 整表正确。
本模块先做字段级评测：对财报表格数值单元格跑 parse_number，统计解析成功率与状态分布，
暴露中文财报数字格式（$ 符号 / 括号负数 / 千分位 / 单位标注）的解析健壮性。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass

from domain import DataStatus
from ingestion.excel_parser import parse_number


@dataclass
class FieldEvalResult:
    total: int = 0
    parsed: int = 0          # present + zero
    parse_errors: int = 0
    status_counter: Counter = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.status_counter is None:
            self.status_counter = Counter()

    @property
    def success_rate(self) -> float:
        return self.parsed / self.total if self.total else 0.0

    @property
    def error_rate(self) -> float:
        return self.parse_errors / self.total if self.total else 0.0


def eval_parse_number(tables: list, skip_header: bool = True, skip_first_col: bool = True) -> FieldEvalResult:
    """对表格数据区域跑 parse_number。

    默认跳过表头行与第一列（通常是标签/公司名，非数值），只测数值单元格。
    """
    result = FieldEvalResult()
    for table in tables:
        rows = table[1:] if skip_header and table else table
        for row in rows:
            if not row:
                continue
            cells = row[1:] if skip_first_col else row
            for cell in cells:
                if cell is None or cell == "":
                    continue
                result.total += 1
                _val, status = parse_number(cell)
                result.status_counter[status.value] += 1
                if status in (DataStatus.PRESENT, DataStatus.ZERO):
                    result.parsed += 1
                elif status is DataStatus.PARSE_ERROR:
                    result.parse_errors += 1
    return result


def load_finqa_tables(path: str, limit: int | None = None) -> list:
    """从 FinQA dev.json 加载 table 列表（每个样本一个 table）。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if limit is not None:
        data = data[:limit]
    return [s.get("table", []) for s in data]


def run_finqa_eval(path: str, limit: int | None = None) -> FieldEvalResult:
    """跑 FinQA 真实财报表格的字段级提取评测。"""
    tables = load_finqa_tables(path, limit)
    return eval_parse_number(tables)
