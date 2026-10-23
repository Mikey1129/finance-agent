"""agent.reviewer —— 归因结论的批判性审查器（方案 5.11「报告输出前校验 gate」）。

归因结论由 AttributionWriter 一次性生成，此前没有「生成后再审查」的语义层 gate。
本模块补上第二道独立 LLM 调用：审查初稿是否可信，并输出修正版结论。

分工（方案核心原则）：
- 引用存在性 / 结构合法性 / refs 越界过滤 = 代码校验，复用 attribution 的
  `build_evidence_context`（证据→编号文本）与 `_validate_conclusion`（结构 + refs 过滤）；
- 证据是否**语义上**真支撑结论 / 相关性是否误写因果 / 是否遗漏反向证据或限制条件 /
  是否把推测写成确定事实 = 交给 LLM 批判性审查（代码无法判断语义支持关系）。

失败降级：无 key / 请求失败 / JSON 非法一律返回 None，调用方直接用初稿，绝不因审查
不可用而崩溃（与 AttributionWriter 同款降级策略）。
"""

from __future__ import annotations

import json
import os
from typing import Any

from config import llm_base_url, llm_model
from agent.attribution import (
    AttributionDraft,
    _extract_json,
    _validate_conclusion,
    build_evidence_context,
)
from agent.context import ContextPolicy
from agent.evidence import EvidencePack

_SYSTEM = (
    "你是企业财报分析 Agent 的结论审查器。下面给出一份归因结论初稿和它引用的证据，"
    "请以批判视角审查并输出修正后的结论。\n"
    "审查规则：\n"
    "1. 每条 reason 的 claim 必须有 refs 支撑，且引用段落语义上真的支持该 claim；"
    "不支持的 reason 直接删除。\n"
    "2. evidence_level 必须恰当：disclosed 必须是报告原文明说的归因，否则降级为 "
    "inferred 或 uncertain。\n"
    "3. 相关性≠因果：报告未明说因果的，改写为「与…相关 / 伴随…变化」的措辞，禁止因果表述。\n"
    "4. 遗漏的反向证据或限制条件补进 caveats。\n"
    "5. 把推测写成确定事实的，降级为 uncertain 或用「可能 / 尚无法确定」措辞。\n"
    "6. 数字只能引用给定证据中已有的数值，绝不编造或推算。\n"
    "输出一个 JSON 对象，字段与初稿一致：summary(一句话总结)、reasons(数组，每项含 "
    "claim/evidence_level/refs/basis)、caveats(字符串数组)。"
)


class ConclusionReviewer:
    """归因结论的批判性审查器：LLM 语义层质检 + 输出修正版结论。

    invoke 可注入（测试 mock 不触网）；httpx 直连 DeepSeek；key 走 DEEPSEEK_API_KEY。
    """

    def __init__(
        self,
        invoke: Any | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        self.model = model or llm_model()
        self.base_url = base_url or llm_base_url()
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self._invoke = invoke or self._default_invoke

    def _default_invoke(self, system: str, user: str) -> str:
        # 与 agent/llm.py 同路线：httpx 直连 DeepSeek OpenAI 兼容端点。
        import httpx

        if not self.api_key:
            return ""
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.0,
                "response_format": {"type": "json_object"},
            },
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def review(
        self, draft: AttributionDraft, pack: EvidencePack, policy: ContextPolicy | None = None
    ) -> AttributionDraft | None:
        """批判性审查初稿，返回修正版结论；任一步失败返回 None（降级用初稿）。"""
        text, mapping = build_evidence_context(pack, policy)
        draft_json = json.dumps(
            draft.conclusion.model_dump(mode="json"), ensure_ascii=False
        )
        user = (
            f"分析问题：{pack.query}\n"
            f"公司：{pack.company_name or '-'}；期间：{pack.periods or '-'}\n\n"
            f"【证据】\n{text}\n\n"
            f"【初稿结论】\n{draft_json}\n\n"
            f"请审查并输出修正后的 JSON。"
        )
        try:
            raw = self._invoke(_SYSTEM, user)
        except Exception:
            return None
        if not raw:
            return None
        data = _extract_json(raw)
        if data is None:
            return None
        conclusion = _validate_conclusion(data, mapping)
        if conclusion is None:
            return None
        return AttributionDraft(conclusion=conclusion, mapping=mapping)


def build_reviewer() -> ConclusionReviewer | None:
    """有 DEEPSEEK_API_KEY 时返回可用的 ConclusionReviewer，否则 None（降级用初稿）。"""
    if os.getenv("DEEPSEEK_API_KEY"):
        return ConclusionReviewer()
    return None
