"""agent.summary —— 历史对话摘要压缩（阶段 5「缓存 / 上下文压缩」，方案 5.5）。

把多轮对话历史拆成「最近 N 轮原始对话」+「更早历史的摘要」。摘要用规则提取
关键实体（公司 / 期间 / 科目 / 指标），只记录「讨论过什么」，不把模型之前生成
的结论当作可信事实（方案 5.5：摘要记录已查事实 + 引用，而非结论）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from finance.metrics import METRIC_DEFINITIONS
from finance.taxonomy import RAW_TO_STANDARD

_COMPANY_RE = re.compile(r"[一-鿿]{2,12}(?:公司|集团|股份|银行|证券)")
_YEAR_RE = re.compile(r"(20\d{2}|19\d{2})\s*年?")


@dataclass
class HistorySummary:
    """历史压缩结果：最近 N 轮原始对话 + 更早历史的实体摘要。"""

    recent: list[str] = field(default_factory=list)
    summary: str = ""


class HistorySummarizer:
    """历史摘要压缩：超出窗口的历史 → 关键实体摘要。"""

    def __init__(self, recent_turns: int = 3):
        self.recent_turns = recent_turns

    def summarize(self, history: list[str]) -> HistorySummary:
        if not history:
            return HistorySummary()
        recent = history[-self.recent_turns :]
        older = history[: -self.recent_turns] if len(history) > self.recent_turns else []
        return HistorySummary(recent=recent, summary=self._build_summary(older))

    @staticmethod
    def _build_summary(older: list[str]) -> str:
        if not older:
            return ""
        text = " ".join(older)
        companies = {m for m in _COMPANY_RE.findall(text)}
        years = {m for m in _YEAR_RE.findall(text)}
        items = {raw for raw in RAW_TO_STANDARD if not raw.isascii() and raw in text}
        metrics = {d.name for d in METRIC_DEFINITIONS.values() if d.name in text}

        parts: list[str] = []
        if companies:
            parts.append("公司=" + ",".join(sorted(companies)))
        if years:
            parts.append("期间=" + ",".join(sorted(years)))
        if items:
            parts.append("科目=" + ",".join(sorted(items)))
        if metrics:
            parts.append("指标=" + ",".join(sorted(metrics)))
        return "此前讨论涉及：" + "；".join(parts) if parts else "此前讨论（无关键实体）"
