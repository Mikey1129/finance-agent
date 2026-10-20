"""agent.state —— AgentState 统一任务状态（方案 5.5）。

核心原则：AgentState ≠ LLM 上下文。State 保存完整任务状态（可能含多次 SQL 查询的
全部结果），但每次调 LLM 只发送当前步骤需要的子集——由 agent.context 的
ContextBuilder 组装，避免 Prompt 随步骤数增长、把先前错误结果反复带入后续推理。

四概念不可混为一谈（方案 5.5）：AgentState（当前任务状态）/ Checkpointer（状态持久化）
/ Memory Store（跨会话长期记忆）/ DuckDB + AnalysisRun（权威业务数据 + 运行记录）。
本模块只定义 AgentState 数据结构，其余由后续模块实现。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from agent.tools import ToolResult
from domain import AccountingStandard


class Intent(str, Enum):
    """意图分类（方案第 7 章三条路径 + 澄清）。"""

    FACT_QUERY = "fact_query"            # 路径 A：精确数值查询
    METRIC_ANALYSIS = "metric_analysis"  # 路径 B：指标与趋势分析
    ATTRIBUTION = "attribution"          # 路径 C：深度归因分析
    CLARIFY = "clarify"                  # 信息不足，需澄清


class AgentStatus(str, Enum):
    """任务执行状态（控制字段）。"""

    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    NEED_CLARIFICATION = "need_clarification"


class AgentState(BaseModel):
    """统一任务状态，字段分组对应方案 5.5 表。

    company_name 对应方案的 company_id（本项目领域模型用 company_name 标识公司）。
    """

    # 会话标识
    thread_id: str = Field(description="持续对话/任务线程 ID")
    run_id: str = Field(description="一次独立执行 ID")
    user_query: str = Field(description="用户原始提问")

    # 业务上下文
    company_name: str | None = Field(default=None, description="分析对象公司")
    report_ids: list[str] = Field(default_factory=list, description="关联的报告 ID")
    periods: list[str] = Field(default_factory=list, description="期间标识，如 ['2024', '2025']")
    accounting_standard: AccountingStandard = AccountingStandard.UNKNOWN

    # 任务状态
    intent: Intent | None = Field(default=None, description="意图分类（router 产出）")
    task_plan: list[str] = Field(default_factory=list, description="任务步骤（planner 产出，仅复杂任务）")
    current_step: int = Field(default=0, description="当前步骤下标")

    # 执行产物
    tool_results: list[ToolResult] = Field(default_factory=list, description="各工具返回结果")
    evidence_refs: list[str] = Field(default_factory=list, description="证据引用 ID")
    validation_results: list[str] = Field(default_factory=list, description="校验结果摘要")

    # 控制字段
    retry_count: int = Field(default=0, description="重试次数（设上限，防无限循环）")
    status: AgentStatus = AgentStatus.IDLE
    final_answer: str | None = Field(default=None, description="最终答案")

    def add_tool_result(self, result: ToolResult) -> None:
        """追加一条工具执行结果。"""
        self.tool_results.append(result)

    def add_validation(self, summary: str) -> None:
        """追加一条校验结果摘要。"""
        self.validation_results.append(summary)
