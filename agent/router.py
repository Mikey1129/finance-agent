"""agent.router —— Intent Router（方案第 7 章）。

第一版规则分流（关键词/模式匹配），不依赖 LLM。三条路径：
  A 精确数值查询（「2025 营收多少」）→ SQL 查结构化数据
  B 指标趋势分析（「近三年净利率变化」）→ 指标注册中心计算
  C 深度归因分析（「为什么经营现金流下降」）→ 规划 + 检索 + 归因
信息不足（无公司/无明确对象）→ CLARIFY。

规则版只做「分流 + 实体提取」，LLM 结构化分类留作阶段 3 引入模型后的增强。
提取出的实体（公司/期间/科目/指标）供后续 ContextBuilder 与工具调度使用。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from agent.state import Intent
from finance.metrics import METRIC_DEFINITIONS
from finance.taxonomy import RAW_TO_STANDARD

# 归因词（路径 C）：优先级最高，即使同时含指标词也归因
_ATTRIBUTION_WORDS = ("为什么", "为何", "原因", "怎么会", "导致", "缘何", "凭什么")

# 趋势/比较词（路径 B 的弱信号，配合科目/期间触发）
_TREND_WORDS = ("趋势", "走势", "同比", "环比", "变化", "增长", "下降", "上升", "对比", "比较", "近")

# 数值查询词（路径 A 的弱信号）
_FACT_WORDS = ("多少", "是多少", "金额", "数值", "几多", "多少元")

# 指标英文别名（name 已是中文，补少量英文/缩写）
_METRIC_ALIASES = {"ROE": "roe", "roe": "roe", "ROA": "roa", "roa": "roa"}

# 公司名粗提取：中文 + 「公司/集团/股份/银行/证券」结尾
_COMPANY_RE = re.compile(r"[一-鿿]{2,12}(?:公司|集团|股份|银行|证券)")

_YEAR_RE = re.compile(r"(20\d{2}|19\d{2})\s*年?")
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_SPAN_RE = re.compile(r"近\s*([0-9一二两三四五六七八九十]+)\s*(?:年|期|个年度)")


class RouterResult(BaseModel):
    """Intent Router 输出：意图 + 提取的业务实体。"""

    intent: Intent
    company_name: str | None = None
    periods: list[str] = Field(default_factory=list, description="绝对年份，如 ['2024', '2025']")
    year_span: int | None = Field(default=None, description="「近 N 年」中的 N")
    standard_items: list[str] = Field(default_factory=list, description="标准科目标识符")
    metric_codes: list[str] = Field(default_factory=list, description="指标 code")
    reason: str = Field(default="", description="命中的规则，便于追溯")
    needs_clarification: bool = False
    clarification: str | None = None


def _extract_periods(query: str) -> list[str]:
    return [m.group(1) for m in _YEAR_RE.finditer(query)]


def _extract_span(query: str) -> int | None:
    m = _SPAN_RE.search(query)
    if not m:
        return None
    raw = m.group(1)
    if raw.isdigit():
        return int(raw)
    return _CN_NUM.get(raw)


def _extract_company(query: str, known_companies: list[str] | None) -> str | None:
    if known_companies:
        for c in known_companies:
            if c in query:
                return c
    m = _COMPANY_RE.search(query)
    return m.group(0) if m else None


def _match_metrics(query: str) -> list[str]:
    codes = [code for code, d in METRIC_DEFINITIONS.items() if d.name in query]
    for alias, code in _METRIC_ALIASES.items():
        if alias in query and code not in codes:
            codes.append(code)
    return codes


def _match_items(query: str) -> list[str]:
    items: list[str] = []
    for raw, std in RAW_TO_STANDARD.items():
        if raw.isascii():  # 跳过英文 key，避免误命中
            continue
        if raw in query and std not in items:
            items.append(std)
    return items


def route(query: str, known_companies: list[str] | None = None) -> RouterResult:
    """规则版意图分流 + 实体提取。"""
    periods = _extract_periods(query)
    year_span = _extract_span(query)
    metric_codes = _match_metrics(query)
    standard_items = _match_items(query)
    company = _extract_company(query, known_companies)

    reasons: list[str] = []
    if any(w in query for w in _ATTRIBUTION_WORDS):
        intent = Intent.ATTRIBUTION
        reasons.append("命中归因词")
    elif metric_codes:
        intent = Intent.METRIC_ANALYSIS
        reasons.append(f"命中指标 {metric_codes}")
    elif any(w in query for w in _TREND_WORDS):
        intent = Intent.METRIC_ANALYSIS
        reasons.append("命中趋势词")
    elif standard_items or any(w in query for w in _FACT_WORDS):
        intent = Intent.FACT_QUERY
        reasons.append(f"命中科目 {standard_items}" if standard_items else "命中数值查询词")
    else:
        intent = Intent.CLARIFY
        reasons.append("未命中任何路径")

    needs_clarification = intent is Intent.CLARIFY or (
        intent is not Intent.CLARIFY and company is None
    )
    clarification = None
    if needs_clarification:
        clarification = "请提供要分析的公司名称" if company is None else "无法理解问题，请补充公司/期间/指标"

    return RouterResult(
        intent=intent,
        company_name=company,
        periods=periods,
        year_span=year_span,
        standard_items=standard_items,
        metric_codes=metric_codes,
        reason="；".join(reasons),
        needs_clarification=needs_clarification,
        clarification=clarification,
    )
