"""retrieval.fact_retriever —— 结构化财务事实检索。

落实方案第 6 章「财务事实由数据层提供」：数值型查询（「存货 / 营收 / 现金流是多少」）
的答案在结构化 financial_facts 里，而非全文文本。本模块把自然语言问句映射到标准科目，
再精确匹配 facts。

背景（评测 C 的架构纠偏）：eval_retrieval 长期用 chunk 全文检索测数值查询，但
「存货余额是多少」这类问句不含答案数字，BM25 词法只能靠高频科目词匹配，命中率有
天花板（实测 Recall@5 卡 0.740）。数值查询本应走 facts 精确检索，chunk 检索只负责
定性 / 归因类查询（「为什么下降」「增长原因」）。本模块补齐这条缺失的检索路径。
"""

from __future__ import annotations

from domain import FinancialFact
from finance.taxonomy import RAW_TO_STANDARD

# 问句常用简称 -> 标准科目标识符（补齐 RAW_TO_STANDARD 未覆盖的口语简称）。
# 只放「归母净利润」这类财报里用全称、问句里用简称的词；不重复 RAW_TO_STANDARD 已有的键。
_QUERY_ALIASES: dict[str, str] = {
    "归母净利润": "net_profit",
    "归母净利": "net_profit",
    "归母利润": "net_profit",
}


def extract_item(query: str) -> str | None:
    """从自然语言问句提取标准科目标识符。

    按科目名长度降序匹配（优先命中更长的全称，如「经营活动产生的现金流量净额」，
    避免被「现金流」等更短子串抢先）。问句简称（_QUERY_ALIASES）覆盖同名标准键。
    未命中返回 None，调用方应回退到 chunk 检索或报告无法识别。
    """
    merged = dict(RAW_TO_STANDARD)
    merged.update(_QUERY_ALIASES)
    for name, std in sorted(merged.items(), key=lambda kv: -len(kv[0])):
        if name in query:
            return std
    return None


class FactRetriever:
    """结构化事实检索器：query → 标准科目 → 匹配 facts（公司/年度/合并范围过滤）。

    默认 scope=consolidated（合并报表口径），与财报主表一致；返回值排序确定性：
    当期（period == fiscal_year）优先，同期间按数值降序（大额合计/归母利润靠前）。
    """

    def __init__(self, facts: list[FinancialFact]):
        self.facts = facts

    def retrieve(
        self,
        query: str,
        company_name: str | None = None,
        fiscal_year: int | None = None,
        scope: str = "consolidated",
        top_k: int = 5,
    ) -> list[FinancialFact]:
        item = extract_item(query)
        if item is None:
            return []
        matched = [
            f for f in self.facts
            if f.standard_item == item
            and (company_name is None or f.company_name == company_name)
            and (fiscal_year is None or f.fiscal_year == fiscal_year)
            and f.consolidation_scope == scope
            and f.has_value()
        ]

        def _key(f: FinancialFact) -> tuple[int, float]:
            is_current = 0 if (fiscal_year is None or f.period == str(fiscal_year)) else 1
            return (is_current, -(f.value or 0.0))

        matched.sort(key=_key)
        return matched[:top_k]
