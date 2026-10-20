"""agent —— Agent 编排层（方案第 5 章）。

阶段 2 已交付：tools（工具注册 + Pydantic Schema + 5 个核心工具）。
阶段 3 进行中：state（AgentState）、router（Intent Router 规则版）。
后续：retriever / context / evidence / hooks / memory / graph / planner。
"""

from .state import AgentState, AgentStatus, Intent
from .router import RouterResult, route
from .evidence import EvidenceAssembler, EvidencePack
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
    "AgentState",
    "AgentStatus",
    "Intent",
    "RouterResult",
    "route",
    "EvidenceAssembler",
    "EvidencePack",
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
