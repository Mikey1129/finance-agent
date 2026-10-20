"""agent —— Agent 编排层（方案第 5 章）。

阶段 2 已交付：tools（工具注册 + Pydantic Schema + 5 个核心工具）。
阶段 3 将交付：state / router / planner / graph / context / evidence / hooks / memory。
"""

from .tools import (
    BaseTool,
    ComputeMetricTool,
    ComparePeriodsTool,
    QueryFinancialFactTool,
    RetrieveEvidenceTool,
    SourceRef,
    ToolError,
    ToolRegistry,
    ToolResult,
    ToolStatus,
    ValidateResultTool,
    build_tools,
)

__all__ = [
    "BaseTool",
    "ComputeMetricTool",
    "ComparePeriodsTool",
    "QueryFinancialFactTool",
    "RetrieveEvidenceTool",
    "SourceRef",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "ToolStatus",
    "ValidateResultTool",
    "build_tools",
]
