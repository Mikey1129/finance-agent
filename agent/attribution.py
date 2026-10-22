"""agent.attribution —— 路径 C 深度归因的结论合成（方案 5.4 / 5.11，阶段 4 补全）。

把 EvidenceAssembler 组好的 EvidencePack（结构化事实 + 指标计算 + 文本证据）交给
DeepSeek，让 LLM 生成带证据等级的归因结论——方案承诺的「分析结论由证据与模型共同
形成」在此落地，是 LLM 在本项目中除「工具编排」外的第二个实质角色。

核心约束（与 agent/llm.py 的 LLMPlanner 一脉相承）：
- 数字绝不靠 LLM 生成：LLM 只能引用给定证据里已有的数值，不得编造/推算；
- 每条原因标证据等级（三区分）：
    disclosed 明确披露  = 报告原文明确给出的归因（带文本引用）；
    inferred  证据推断  = 报告未明说，由数据方向 + 证据合理推断（相关性措辞，禁因果）；
    uncertain 待证假设  = 证据不足、无法确定（明确标注「尚无法确定」，不硬答）；
- 相关性≠因果：只有报告原文明确「X 导致 Y」或数据方向与附注归因相互印证才用因果措辞；
- key 从环境变量 DEEPSEEK_API_KEY 读取，缺 key / 请求失败 / JSON 非法一律返回 None，
  调用方降级为模板拼接，绝不因 LLM 不可用崩溃。

复用 agent/llm.py 的 httpx 直连 DeepSeek 模式（绕开 openai SDK 兼容性 bug）。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from config import llm_base_url, llm_model
from agent.context import ContextPolicy
from agent.evidence import EvidencePack
from agent.tools import SourceRef

_SYSTEM = (
    "你是企业财报分析 Agent 的归因结论撰写器。根据给定的财务事实、指标计算和文本证据，"
    "撰写深度归因结论。\n"
    "硬性规则：\n"
    "1. 数字只能引用给定证据中已有的数值，绝不编造或推算新数字。\n"
    "2. 每条原因必须标注 evidence_level，三选一：\n"
    "   - disclosed（明确披露）：报告原文明确给出的归因，refs 必须含对应文本证据 [C#]。\n"
    "   - inferred（证据推断）：报告未明说，由财务事实/指标变化方向合理推断，refs 含 [F#]/[M#]，"
    "措辞用「与…相关 / 伴随…变化」，禁止写成因果关系。\n"
    "   - uncertain（待证假设）：证据不足或无法确定，明确标注「尚无法确定」，refs 可为空。\n"
    "3. 相关性≠因果：只有报告原文明确说「X 导致 Y」或数据方向与附注归因相互印证时，才可用因果措辞；"
    "否则一律用相关性措辞。\n"
    "4. 证据不足时在 caveats 里明确说明，不硬凑结论。\n"
    "5. refs 只能引用证据里出现的编号（[F1]/[M1]/[C1] 等）。\n"
    "输出一个 JSON 对象，字段：summary(一句话总结)、reasons(数组，每项含 "
    "claim/evidence_level/refs/basis)、caveats(字符串数组)。"
)


class EvidenceLevel(str, Enum):
    """归因结论的证据等级（方案「每条原因标证据等级」的三区分落地）。"""

    DISCLOSED = "disclosed"  # 明确披露：报告原文明确给出的归因
    INFERRED = "inferred"    # 证据推断：数据方向 + 证据的合理推断（相关性措辞）
    UNCERTAIN = "uncertain"  # 待证假设：证据不足、无法确定


class AttributionReason(BaseModel):
    """一条归因原因：主张 + 证据等级 + 引用编号 + 依据。"""

    claim: str
    evidence_level: EvidenceLevel
    refs: list[str] = Field(default_factory=list, description="证据编号，如 ['F1','C2']")
    basis: str = Field(default="", description="依据简述，可追溯")


class AttributionConclusion(BaseModel):
    """LLM 生成的结构化归因结论。"""

    summary: str = ""
    reasons: list[AttributionReason] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


@dataclass
class AttributionDraft:
    """结论 + 编号→来源映射（渲染报告时用）。"""

    conclusion: AttributionConclusion
    mapping: dict[str, SourceRef]


def build_evidence_context(
    pack: EvidencePack, policy: ContextPolicy | None = None
) -> tuple[str, dict[str, SourceRef]]:
    """把 EvidencePack 转成 LLM 可读的紧凑编号文本 + 编号→SourceRef 映射。

    只取「有值事实」（has_value）、「已算出指标」（computed）、前 N 条文本证据
    （policy.max_evidence_chunks），并附冲突/缺失提示——这些是 LLM 组织结论的全部依据。
    """
    policy = policy or ContextPolicy()
    mapping: dict[str, SourceRef] = {}
    lines: list[str] = []

    fact_lines: list[str] = []
    fi = 0
    for f in pack.financial_facts:
        if not f.has_value():
            continue
        fi += 1
        ref = f"F{fi}"
        mapping[ref] = SourceRef(kind="fact", ref_id=f.fact_id, report_id=f.report_id)
        fact_lines.append(
            f"[{ref}] {f.standard_item}={f.value}元 (期间 {f.period or '-'}, {f.consolidation_scope})"
        )
    if fact_lines:
        lines.append("【财务事实】")
        lines.extend(fact_lines)

    metric_lines: list[str] = []
    mi = 0
    for code, m in pack.computed_metrics.items():
        if not getattr(m, "computed", False):
            continue
        mi += 1
        ref = f"M{mi}"
        mapping[ref] = SourceRef(kind="metric", ref_id=code)
        metric_lines.append(
            f"[{ref}] {m.name}={m.value:.4f} (期间 {m.period}, {m.scope})"
        )
    if metric_lines:
        lines.append("【指标计算】")
        lines.extend(metric_lines)

    chunk_lines: list[str] = []
    ci = 0
    for c in pack.text_evidence[: policy.max_evidence_chunks]:
        ci += 1
        ref = f"C{ci}"
        mapping[ref] = SourceRef(
            kind="chunk", ref_id=c.chunk_id, report_id=c.report_id, page=c.page_start
        )
        loc = f"p{c.page_start} " if c.page_start is not None else ""
        loc += c.section_path or ""
        chunk_lines.append(f"[{ref}] ({loc.strip()}) \"{c.text}\"")
    if chunk_lines:
        lines.append("【文本证据】")
        lines.extend(chunk_lines)

    if pack.contradictions:
        lines.append("【冲突】")
        lines.extend(f"- {x}" for x in pack.contradictions)
    if pack.missing_evidence:
        lines.append("【缺失证据】")
        lines.extend(f"- {x}" for x in pack.missing_evidence)

    return "\n".join(lines), mapping


def _extract_json(content: str) -> dict | None:
    """从 LLM 返回内容里容错提取 JSON（剥 markdown 代码块 / 前后杂文）。"""
    if not content:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.DOTALL)
    if m:
        content = m.group(1)
    else:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end < start:
            return None
        content = content[start : end + 1]
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _validate_conclusion(data: dict, mapping: dict[str, SourceRef]) -> AttributionConclusion | None:
    """校验 LLM 输出的 JSON 并构造结论；level 非法或 reason 全无效则返回 None（降级）。"""
    reasons_raw = data.get("reasons")
    if not isinstance(reasons_raw, list):
        return None

    valid_levels = {e.value for e in EvidenceLevel}
    valid_refs = set(mapping.keys())
    reasons: list[AttributionReason] = []
    for r in reasons_raw:
        if not isinstance(r, dict):
            continue
        claim = r.get("claim")
        level = r.get("evidence_level")
        if not claim or level not in valid_levels:
            continue  # 单条非法丢弃，不影响其余
        refs = r.get("refs") or []
        if not isinstance(refs, list):
            refs = []
        # 过滤越界/未知引用编号，避免渲染悬空引用
        refs = [x for x in refs if isinstance(x, str) and x in valid_refs]
        reasons.append(
            AttributionReason(
                claim=str(claim),
                evidence_level=EvidenceLevel(level),
                refs=refs,
                basis=str(r.get("basis") or ""),
            )
        )
    if not reasons:
        return None

    caveats = [str(c) for c in (data.get("caveats") or []) if isinstance(c, str)]
    return AttributionConclusion(
        summary=str(data.get("summary") or ""),
        reasons=reasons,
        caveats=caveats,
    )


_LEVEL_LABELS = {
    EvidenceLevel.DISCLOSED: "明确披露",
    EvidenceLevel.INFERRED: "证据推断",
    EvidenceLevel.UNCERTAIN: "待证假设",
}


def render_report(draft: AttributionDraft) -> tuple[str, list[SourceRef]]:
    """把结构化结论渲染成给用户的 markdown 报告 + 结构化引用列表（可追溯）。"""
    conclusion = draft.conclusion
    mapping = draft.mapping
    lines: list[str] = []

    if conclusion.summary:
        lines.append(f"## 结论\n{conclusion.summary}")
    lines.append("## 归因分析")
    for i, r in enumerate(conclusion.reasons, start=1):
        label = _LEVEL_LABELS.get(r.evidence_level, r.evidence_level.value)
        refs = " ".join(f"[{x}]" for x in r.refs) if r.refs else ""
        lines.append(f"{i}. **[{label}]** {r.claim} {refs}".rstrip())
        if r.basis:
            lines.append(f"   - 依据：{r.basis}")

    if conclusion.caveats:
        lines.append("## 说明")
        lines.extend(f"- {c}" for c in conclusion.caveats)

    # 来源清单：只列结论实际引用到的编号，按 F/M/C 顺序
    used_refs = [x for x in mapping if any(x in r.refs for r in conclusion.reasons)]
    sources = [mapping[x] for x in used_refs]
    if used_refs:
        lines.append("## 来源")
        for ref in used_refs:
            src = mapping[ref]
            if src.kind == "fact":
                lines.append(f"- [{ref}] 财务事实 {src.ref_id}")
            elif src.kind == "metric":
                lines.append(f"- [{ref}] 指标 {src.ref_id}")
            else:
                page = f" p{src.page}" if src.page is not None else ""
                lines.append(f"- [{ref}] 文本证据 {src.ref_id}{page}")

    return "\n".join(lines), sources


class AttributionWriter:
    """LLM 归因结论撰写器。invoke 可注入（测试 mock 不触网），失败返回 None（降级）。"""

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

    def write(self, pack: EvidencePack, policy: ContextPolicy | None = None) -> AttributionDraft | None:
        """组装证据上下文 → 调 LLM → 校验 → 返回结论草稿；任一步失败返回 None（降级）。"""
        text, mapping = build_evidence_context(pack, policy)
        user = (
            f"分析问题：{pack.query}\n"
            f"公司：{pack.company_name or '-'}；期间：{pack.periods or '-'}\n\n"
            f"{text}\n\n请输出 JSON。"
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


def build_attribution_writer() -> AttributionWriter | None:
    """有 DEEPSEEK_API_KEY 时返回可用的 AttributionWriter，否则 None（降级模板拼接）。"""
    if os.getenv("DEEPSEEK_API_KEY"):
        return AttributionWriter()
    return None
