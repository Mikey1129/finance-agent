"""ReportDoc —— 报告级领域模型。

对应方案第 6 章「数据模型」第 1 类。描述一份财报报告的元信息，
不承担具体财务数值的存储（数值在 FinancialFact）。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class AccountingStandard(str, Enum):
    """会计准则。中文财报优先 CAS，留 IFRS / US GAAP 接口。"""

    CAS = "CAS"          # 中国会计准则
    IFRS = "IFRS"        # 国际财务报告准则
    US_GAAP = "US_GAAP"  # 美国通用会计准则
    UNKNOWN = "UNKNOWN"


class ReportType(str, Enum):
    ANNUAL = "annual"        # 年报
    INTERIM = "interim"      # 中报（半年报）
    QUARTERLY = "quarterly"  # 季报
    OTHER = "other"


class ParseStatus(str, Enum):
    """解析状态（阶段 1 数据接入时更新）。"""

    PENDING = "pending"
    PARSED = "parsed"
    FAILED = "failed"


class ReportDoc(BaseModel):
    """一份财报报告（报告级元数据 + 版本追溯）。"""

    report_id: str = Field(description="全局唯一报告 ID")
    company_name: str = Field(description="公司名称")
    fiscal_year: int = Field(description="财务年度，如 2025")

    accounting_standard: AccountingStandard = AccountingStandard.UNKNOWN
    currency: str = "CNY"
    report_type: ReportType = ReportType.ANNUAL
    consolidation_scope: str = "consolidated"  # consolidated=合并报表 / parent_only=母公司单体

    # 文件与来源
    source_file: str | None = Field(default=None, description="原始文件路径或文件名")
    file_hash: str | None = Field(default=None, description="文件哈希，用于去重与追溯")

    # 版本（规则 6：重述保留版本，不直接覆盖）
    version: int = 1
    parse_status: ParseStatus = ParseStatus.PENDING

    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def key(self) -> str:
        """业务唯一键：公司 + 年度 + 合并范围 + 版本。"""
        return f"{self.company_name}|{self.fiscal_year}|{self.consolidation_scope}|v{self.version}"
