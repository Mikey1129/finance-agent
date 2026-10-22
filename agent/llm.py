"""agent.llm —— LLM 决策层（方案 5.4 Function Calling，阶段 4）。

确定性优先（方案 5.2 四原则）：简单查询/指标走规则 Planner，只有深度归因等需要
LLM 动态规划的任务才启用 LLM 决策层。本模块封装 DeepSeek 的 Function Calling：
把工具 schema 交给 LLM，让其决定调用哪个工具、传什么参数。财务数字仍由数据层/
计算层产生，LLM 只做「决定调用」，不生成数字（方案「数字绝不靠 LLM 生成」）。

key 从环境变量 DEEPSEEK_API_KEY 读取（不硬编码、不入库）。真实调用可注入替换，
测试用 FakeChat 不触网。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from config import llm_base_url, llm_model

_SYSTEM = (
    "你是企业财报分析 Agent 的任务规划器。根据用户问题和可用工具，决定调用哪些工具"
    "及其参数。必须调用工具完成规划，不要用文字直接回答财务问题；财务数字必须由工具"
    "从数据层/计算层取得，不得自行编造。"
)


@dataclass
class PlannedCall:
    """一次 LLM 规划的工具调用。"""

    tool: str
    args: dict = field(default_factory=dict)


def _to_openai_tools(schemas: list[dict]) -> list[dict]:
    """把 ToolRegistry.schemas() 的 {name,description,parameters} 转为 OpenAI function 格式。"""
    return [
        {
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s["description"],
                "parameters": s["parameters"],
            },
        }
        for s in schemas
    ]


def parse_tool_calls(raw_calls: list[Any]) -> list[PlannedCall]:
    """解析 LLM 返回的 tool_calls（dict 列表，args 可能为 JSON 字符串），容错解析。"""
    planned: list[PlannedCall] = []
    for tc in raw_calls or []:
        name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
        args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None)
        if not name:
            continue
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        planned.append(PlannedCall(tool=name, args=args))
    return planned


class LLMPlanner:
    """LLM Function Calling 规划器。invoke 可注入（默认真实 DeepSeek）。"""

    def __init__(
        self,
        invoke: Callable[..., list[Any]] | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        self.model = model or llm_model()
        self.base_url = base_url or llm_base_url()
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self._invoke = invoke or self._default_invoke

    def _default_invoke(self, system: str, user: str, tools: list[dict]) -> list[Any]:
        # 直接用 httpx 调 DeepSeek 的 OpenAI 兼容端点，绕开 openai SDK / langchain 与
        # 本机 httpx 版本的兼容性 bug（openai 3.5.0 报 "process() takes no keyword
        # arguments"）。返回 [{"name":..., "args": JSON字符串}]，交给 parse_tool_calls。
        import httpx

        if not self.api_key:
            raise RuntimeError("未设置环境变量 DEEPSEEK_API_KEY，无法初始化 LLM 决策层")
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "tools": tools,
                "temperature": 0.0,
            },
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=60.0,
        )
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        return [
            {"name": tc["function"]["name"], "args": tc["function"]["arguments"]}
            for tc in (msg.get("tool_calls") or [])
        ]

    def plan(
        self,
        query: str,
        tool_schemas: list[dict],
        company_name: str | None = None,
        periods: list[str] | None = None,
    ) -> list[PlannedCall]:
        """根据问题与工具 schema 规划工具调用序列。"""
        user = query
        if company_name:
            user = f"分析对象：{company_name}；期间：{periods or []}。问题：{query}"
        raw = self._invoke(_SYSTEM, user, _to_openai_tools(tool_schemas))
        return parse_tool_calls(raw)


def build_llm_planner() -> LLMPlanner | None:
    """有 DEEPSEEK_API_KEY 时返回可用的 LLMPlanner，否则 None（降级规则 Planner）。

    生产环境（app/service.py）据此注入：无 key 时复杂度门控自动落到规则规划，行为
    与未接入 LLM 前一致。
    """
    if os.getenv("DEEPSEEK_API_KEY"):
        return LLMPlanner()
    return None
