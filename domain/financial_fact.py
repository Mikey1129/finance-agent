"""FinancialFact —— 单条标准化财务事实。

对应方案第 6 章「数据模型」第 2 类，是最核心的领域模型。
落实第 6 章的 7 条财务数据规则：
  1. 货币与单位分开存，内部统一单位（value 统一为「元」，unit 记录原始单位）
  2. 区分年度/季度/年初至今/时点（period_type）
  3. 合并 vs 母公司单体（consolidation_scope）
  4. 保留原始值 + 标准化值（raw_value vs value），派生指标单独标记
  5. 缺失/零/不适用/未披露/解析失败分别表示（data_status）
  6. 重述保留版本（version）
  7. 映射/公式规则可追溯（mapping_rule_version）
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class DataStatus(str, Enum):
    """数据状态（规则 5）。"""

    PRESENT = "present"                  # 正常数值
    MISSING = "missing"                  # 缺失
    ZERO = "zero"                        # 零值
    NOT_APPLICABLE = "not_applicable"    # 不适用
    NOT_DISCLOSED = "not_disclosed"      # 未披露
    PARSE_ERROR = "parse_error"          # 解析失败


class PeriodType(str, Enum):
    """期间类型（规则 2）。"""

    ANNUAL = "annual"                # 年度累计
    QUARTERLY = "quarterly"          # 季度累计
    YTD = "ytd"                      # 年初至今
    POINT_IN_TIME = "point_in_time"  # 时点（资产负债表期末值）


class StatementType(str, Enum):
    BALANCE_SHEET = "balance_sheet"      # 资产负债表
    INCOME_STATEMENT = "income_statement"  # 利润表
    CASH_FLOW = "cash_flow"              # 现金流量表
    OTHER = "other"


class FinancialFact(BaseModel):
    """一条标准化财务事实。"""

    fact_id: str = Field(description="全局唯一事实 ID")
    report_id: str = Field(description="关联 ReportDoc.report_id")
    company_name: str
    fiscal_year: int

    # 科目（规则 4/7：原始 + 标准都保留）
    raw_item: str = Field(description="财报原文科目名")
    standard_item: str = Field(description="映射后的标准科目名")

    # 数值与单位（规则 1：单位与数值分开）
    value: float | None = Field(default=None, description="标准化数值（内部统一为「元」）")
    raw_value: float | None = Field(default=None, description="原始数值（未做单位换算）")
    unit: str | None = Field(default=None, description="原始单位：元/千元/万元/亿元")
    currency: str = "CNY"

    # 期间（规则 2）
    period: str | None = Field(default=None, description="期间标识，如 2025-12-31 或 2025")
    period_type: PeriodType = PeriodType.ANNUAL

    # 报表与合并范围（规则 3）
    statement_type: StatementType = StatementType.OTHER
    consolidation_scope: str = "consolidated"

    # 数据状态（规则 5）
    data_status: DataStatus = DataStatus.PRESENT

    # 版本与规则追溯（规则 6/7）
    version: int = 1
    mapping_rule_version: str | None = Field(default=None, description="科目映射规则版本")
    is_derived: bool = Field(default=False, description="是否派生指标（非直接取自报表）")

    # 来源血缘
    provenance_id: str | None = Field(default=None, description="关联 Provenance.provenance_id")

    def has_value(self) -> bool:
        """是否携带可用数值。"""
        return self.data_status is DataStatus.PRESENT and self.value is not None
