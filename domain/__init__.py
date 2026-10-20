"""领域模型：财报报告、财务事实、来源血缘。

三模型是项目最重要的基础（见方案第 6 章）：
- ReportDoc：报告级信息（公司/年份/准则/货币/版本/状态）
- FinancialFact：单条标准化财务事实（科目/数值/单位/期间/报表类型/数据状态）
- Provenance：来源血缘（文件/页码/表位/单元格/提取方法/转换步骤）
"""

from .report_doc import (
    AccountingStandard,
    ParseStatus,
    ReportDoc,
    ReportType,
)
from .financial_fact import (
    DataStatus,
    FinancialFact,
    PeriodType,
    StatementType,
)
from .provenance import Provenance
from .evidence_chunk import ChunkType, EvidenceChunk
from .analysis_run import AnalysisRun, ToolCallRecord

__all__ = [
    "AccountingStandard",
    "ParseStatus",
    "ReportDoc",
    "ReportType",
    "DataStatus",
    "FinancialFact",
    "PeriodType",
    "StatementType",
    "Provenance",
    "ChunkType",
    "EvidenceChunk",
    "AnalysisRun",
    "ToolCallRecord",
]
