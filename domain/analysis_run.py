"""AnalysisRun —— 一次分析任务的运行记录（方案 5.5 / 6 章「可回放」）。

AnalysisRun 是权威业务数据侧（DuckDB）的审计记录，与另外三类存储严格分离：
  - AgentState：当前任务内存态（agent/state.py）
  - Checkpointer：会话状态快照持久化（agent/checkpoint.py）
  - MemoryStore：跨会话长期记忆（agent/memory.py）

记录内容对应方案「可追踪执行过程」：输入、数据版本、规则版本、工具调用、
来源引用、模型/Prompt 版本、校验结果、输出。LLM 随机性不保证逐字复现，
但执行过程可完整追踪。规则 7 的可追溯由此落地。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ToolCallRecord(BaseModel):
    """一次工具调用记录（审计摘要，不含财报全文/大段文本）。"""

    tool: str = Field(description="工具名")
    status: str = Field(description="执行状态 success/error/no_result")
    summary: str = Field(default="", description="人类可读结果摘要")
    source_refs: list[str] = Field(
        default_factory=list, description="来源引用 fact:<id> / metric:<code> / chunk:<id>"
    )


class AnalysisRun(BaseModel):
    """一次分析任务运行记录。"""

    run_id: str = Field(description="运行唯一 ID")
    thread_id: str = Field(description="所属会话线程 ID")
    user_id: str | None = Field(default=None, description="触发用户 ID（阶段 5 权限接入）")
    user_query: str = Field(description="用户原始提问")

    company_name: str | None = None
    periods: list[str] = Field(default_factory=list, description="分析期间")
    intent: str | None = Field(default=None, description="意图分类")
    status: str = Field(description="success / failed / need_clarification")

    plan: list[dict] = Field(default_factory=list, description="规划的工具步骤（含参数）")
    tool_calls: list[ToolCallRecord] = Field(default_factory=list, description="工具调用记录")
    validation_summary: list[str] = Field(default_factory=list, description="校验/冲突/缺失摘要")
    final_answer: str | None = Field(default=None, description="最终答案")

    # 可回放版本信息（方案 5.5）
    data_version: str | None = Field(default=None, description="数据版本")
    rule_version: str | None = Field(default=None, description="规则/口径版本")
    model_version: str | None = Field(default=None, description="模型版本")
    prompt_version: str | None = Field(default=None, description="Prompt 版本")

    created_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = Field(default=None, description="结束时间")
