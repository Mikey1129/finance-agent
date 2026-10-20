"""agent.hooks —— 三层校验 Hook（方案 5.8）。

三层保障机制（Hook 不能代替工具内部的安全检查与最终结果验证）：
- 工具入口层（before）：参数验证、范围限制、预算检查、权限校验 → 阻止非法调用；
- 工具执行层：业务规则/SQL 约束/公式正确性在工具内部实现（见 agent/tools.py）；
- 工作流层（after）：结果校验、来源完整性 → 对已产生的结果追加 warning。

第一版用进程内 HookManager 包在 ToolRegistry.invoke 外，不依赖 LangGraph 中间件。
上传的财报和检索文本应视为不可信数据，检索文本中的可疑指令在 after 层标记。

阶段 5 新增 PermissionHook（工具入口层权限校验）：Hook.before/after 统一加 user
参数（默认 None=未启用权限，行为与接入前一致）；HookManager.invoke 把当前用户
透传给每个 Hook。
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass

from agent.tools import ToolRegistry, ToolResult, ToolStatus
from auth import AccessPolicy, User


@dataclass
class HookResult:
    """Hook 判定结果。"""

    ok: bool = True
    message: str | None = None


class Hook(ABC):
    """工具调用 Hook 接口。before 在调用前，after 在调用后。user 为当前用户（None=未启用权限）。"""

    name: str = "hook"

    def before(self, tool_name: str, args: dict, user: User | None = None) -> HookResult:
        return HookResult()

    def after(self, tool_name: str, args: dict, result: ToolResult, user: User | None = None) -> HookResult:
        return HookResult()


# ---------------------------------------------------------------------------
# 入口层：参数范围限制
# ---------------------------------------------------------------------------
class ParamRangeHook(Hook):
    """参数验证与调用范围限制（方案 5.8 工具入口层）。"""

    name = "param_range"

    def before(self, tool_name: str, args: dict, user: User | None = None) -> HookResult:
        fy = args.get("fiscal_year")
        if fy is not None and not (1900 <= int(fy) <= 2100):
            return HookResult(False, f"fiscal_year 超出合理范围: {fy}")
        query = args.get("query")
        if query is not None and len(str(query)) > 500:
            return HookResult(False, "query 长度超过 500")
        top_k = args.get("top_k")
        if top_k is not None and not (1 <= int(top_k) <= 20):
            return HookResult(False, f"top_k 超出 [1, 20]: {top_k}")
        return HookResult()


# ---------------------------------------------------------------------------
# 入口层：权限校验（阶段 5）
# ---------------------------------------------------------------------------
class PermissionHook(Hook):
    """工具入口层权限校验：用户是否有权访问工具涉及的公司（方案 5.4 + 阶段 5）。

    user=None 时不检查（未启用权限模式）；user 有值时，对 args 中的 company_name
    / company_names 逐个做读权限判定，越权即阻止调用。
    """

    name = "permission"

    def __init__(self, policy: AccessPolicy | None = None):
        self.policy = policy or AccessPolicy()

    def before(self, tool_name: str, args: dict, user: User | None = None) -> HookResult:
        if user is None:
            return HookResult()
        for company in self._companies(args):
            if company and not self.policy.can_read(user, company):
                return HookResult(False, f"无权访问公司: {company}")
        return HookResult()

    @staticmethod
    def _companies(args: dict) -> list[str]:
        """从工具参数里收集涉及的公司名（单值 company_name 或列表 company_names）。"""
        names: list[str] = []
        raw = args.get("company_names")
        if isinstance(raw, str):
            names.append(raw)
        elif raw:
            names.extend(raw)
        single = args.get("company_name")
        if single:
            names.append(single)
        return names


# ---------------------------------------------------------------------------
# 工作流层：预算（调用次数上限）
# ---------------------------------------------------------------------------
class BudgetHook(Hook):
    """限制工具调用次数，防止无限循环（方案 5.2 所有重试设上限）。"""

    name = "budget"

    def __init__(self, max_calls: int = 10):
        self.max_calls = max_calls
        self.calls = 0

    def before(self, tool_name: str, args: dict, user: User | None = None) -> HookResult:
        if self.calls >= self.max_calls:
            return HookResult(False, f"超过最大工具调用次数 {self.max_calls}")
        return HookResult()

    def after(self, tool_name: str, args: dict, result: ToolResult, user: User | None = None) -> HookResult:
        self.calls += 1
        return HookResult()


# ---------------------------------------------------------------------------
# 工作流层：结果校验
# ---------------------------------------------------------------------------
class SourceIntegrityHook(Hook):
    """来源完整性：返回数据的工具必须有来源引用（方案 5.9 第 2 条）。"""

    name = "source_integrity"

    _DATA_TOOLS = {
        "query_financial_fact",
        "compute_metric",
        "compare_periods",
        "compare_companies",
        "retrieve_evidence",
    }

    def after(self, tool_name: str, args: dict, result: ToolResult, user: User | None = None) -> HookResult:
        if (
            result.status is ToolStatus.SUCCESS
            and tool_name in self._DATA_TOOLS
            and result.data
            and not result.sources
        ):
            return HookResult(False, "返回数据但缺少来源引用")
        return HookResult()


class PromptInjectionHook(Hook):
    """检索文本视为不可信数据：标记可疑指令（方案 5.8）。"""

    name = "prompt_injection"

    _SUSPICIOUS = ("忽略以上", "忽略之前", "system:", "你是我的", "从现在起")

    def after(self, tool_name: str, args: dict, result: ToolResult, user: User | None = None) -> HookResult:
        if tool_name != "retrieve_evidence" or result.status is not ToolStatus.SUCCESS:
            return HookResult()
        for chunk in result.data or []:
            text = getattr(chunk, "text", "")
            for pat in self._SUSPICIOUS:
                if pat in text:
                    return HookResult(False, f"检索文本含可疑指令: {pat!r}")
        return HookResult()


# ---------------------------------------------------------------------------
# HookManager
# ---------------------------------------------------------------------------
class HookManager:
    """按顺序执行 before/after Hook 包住工具调用。invoke 的 user 透传给每个 Hook。"""

    def __init__(self, hooks: list[Hook] | None = None):
        self.hooks = hooks or []

    def invoke(self, registry: ToolRegistry, tool_name: str, user: User | None = None, **kwargs) -> ToolResult:
        # before：任一失败则阻止调用
        for hook in self.hooks:
            r = hook.before(tool_name, kwargs, user=user)
            if not r.ok:
                return ToolResult.fail(tool_name, "hook_blocked", f"[{hook.name}] {r.message}")
        result = registry.invoke(tool_name, **kwargs)
        # after：结果校验失败追加 warning（结果已产生，不丢弃数据）
        for hook in self.hooks:
            r = hook.after(tool_name, kwargs, result, user=user)
            if not r.ok:
                result.warnings.append(f"[{hook.name}] {r.message}")
        return result
