"""eval_retrieval —— RAG 检索评测（方案第 9 章「RAG 检索评测提前到阶段 3」）。

指标：Recall@K / Precision@K / MRR / nDCG@K，另有 Retrieval Latency。
评测集：人工标注的一批真实财务问题 + 正确证据（gold）文本锚点。

gold 用「文本锚点」而非硬编码 chunk_id：把 chunk 文本去空白后做子串匹配，凡命中任一
gold 短语的 chunk 即为「正确证据」。这样切块逻辑调整后评测集语义不变（只需重新匹配），
不因 chunk_id 变化而失效。

本模块先跑 BM25 基线（第一版无 BGE/Reranker，方案 5.11「先 BM25 基线 + 评测集，再加
BGE」）；后续 BGE/Reranker 接入后复用同一评测集对比提升。**不以「回答流畅」判断好坏。**

BM25 基线（公牛集团 2024 年报，606 chunk，10 查询，k=5）：
Recall@5=0.632 / Precision@5=0.300 / MRR=0.667 / nDCG@5=0.534 / Latency≈17ms。
弱项集中在精确数值查询（营收/存货/货币资金），BM25 词法 2-gram 匹配不到资产负债表
数值行、反而命中附注讨论——正是 BGE 语义召回预期改善的方向。
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field

from domain import FinancialFact, StatementType
from ingestion.pdf_parser import parse_pdf
from retrieval.chunker import chunk_pages
from retrieval.fact_retriever import FactRetriever
from retrieval.retriever import Retriever


# -- 评测集 ---------------------------------------------------------------
@dataclass
class RetrievalQuery:
    """一个检索评测查询：问题 + gold 证据。

    kind="chunk"：gold 为文本锚点（gold_phrases），走全文 chunk 检索（定性/归因类）。
    kind="fact"：gold 为标准科目 + 目标数值（元），走结构化 facts 检索（数值类）。
    """

    qid: str
    query: str
    gold_phrases: list[str]  # chunk 检索的文本锚点（任一短语命中即视为 gold 证据）
    kind: str = "chunk"       # "chunk" | "fact"
    gold_item: str | None = None                # fact 检索：标准科目标识符
    gold_values: list[float] = field(default_factory=list)  # fact 检索：目标数值（元）


# 公牛集团 2024 年报真实财务问题 + 正确证据锚点（人工标注，锚点取自年报正文原文）。
# 锚点优先用「精确数值/独特短语」而非「科目+数值」严格连接，避免因措辞差异（如
# 「营业收入为人民币168.31 亿元」）漏标正确证据。
BUILTIN_QUERIES: list[RetrievalQuery] = [
    RetrievalQuery("q01", "公牛集团2024年营业收入是多少？", ["168.31亿元"],
                   kind="fact", gold_item="revenue", gold_values=[16_830_541_086.13]),
    RetrievalQuery("q02", "2024年归母净利润是多少？同比增长多少？", ["42.72亿元"],
                   kind="fact", gold_item="net_profit_parent", gold_values=[4_272_204_565.03]),
    RetrievalQuery("q03", "2024年经营活动产生的现金流量净额是多少？", ["3,730,346,896.27"],
                   kind="fact", gold_item="operating_cash_flow", gold_values=[3_730_346_896.27]),
    RetrievalQuery("q04", "经营活动现金流量净额为什么下降？", ["预收货款下降"]),
    RetrievalQuery("q05", "2024年度利润分配方案是什么？", ["每10股派发现金红利24.00元"]),
    RetrievalQuery("q06", "2024年末存货余额是多少？", ["1,573,672,819.32"],
                   kind="fact", gold_item="inventory", gold_values=[1_573_672_819.32]),
    RetrievalQuery("q07", "2024年公司整体毛利率是多少？", ["43.27"]),
    RetrievalQuery("q08", "2024年研发费用投入多少？", ["745,510,215.17"],
                   kind="fact", gold_item="rnd_expenses", gold_values=[745_510_215.17]),
    RetrievalQuery("q09", "新能源业务收入及增速如何？", ["新能源业务收入7.77亿元", "104.75%"]),
    RetrievalQuery("q10", "2024年末货币资金余额是多少？", ["5,020,380,352.96"],
                   kind="fact", gold_item="cash_and_equivalents", gold_values=[5_020_380_352.96]),
]


# -- 检索指标（纯函数，可独立单测） ---------------------------------------
def _norm(s: str) -> str:
    """去所有空白（含换行），用于稳健的子串匹配。"""
    return re.sub(r"\s+", "", s)


def gold_chunk_ids(chunks, phrases: list[str]) -> set[str]:
    """返回文本命中任一 gold 短语（去空白后）的 chunk_id 集合。"""
    norm_phrases = [_norm(p) for p in phrases]
    ids: set[str] = set()
    for c in chunks:
        t = _norm(c.text)
        if any(p in t for p in norm_phrases):
            ids.add(c.chunk_id)
    return ids


def recall_at_k(ranked_ids: list[str], gold_ids: set[str], k: int) -> float:
    """Recall@K：gold 证据进入前 K 的比例。"""
    if not gold_ids:
        return 0.0
    return len(set(ranked_ids[:k]) & gold_ids) / len(gold_ids)


def precision_at_k(ranked_ids: list[str], gold_ids: set[str], k: int) -> float:
    """Precision@K：前 K 结果中 gold 证据占比。"""
    if k <= 0:
        return 0.0
    return len(set(ranked_ids[:k]) & gold_ids) / k


def mrr(ranked_ids: list[str], gold_ids: set[str]) -> float:
    """MRR：第一个 gold 证据排名的倒数（无则 0）。"""
    for i, cid in enumerate(ranked_ids, start=1):
        if cid in gold_ids:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked_ids: list[str], gold_ids: set[str], k: int) -> float:
    """nDCG@K：gold 命中位置的对数折损增益（gold 视为二值相关）。"""
    dcg = sum(
        1.0 / math.log2(i + 2) for i, cid in enumerate(ranked_ids[:k]) if cid in gold_ids
    )
    ideal_hits = min(len(gold_ids), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg else 0.0


# -- 评测结果 ---------------------------------------------------------------
@dataclass
class QueryResult:
    qid: str
    query: str
    n_gold: int
    ranked_ids: list[str]
    gold_ids: set[str]
    kind: str = "chunk"  # "chunk" | "fact"
    recall: float = 0.0
    precision: float = 0.0
    mrr: float = 0.0
    ndcg: float = 0.0
    latency_ms: float = 0.0


@dataclass
class RetrievalEvalResult:
    k: int
    queries: list[QueryResult] = field(default_factory=list)

    def _avg(self, attr: str) -> float:
        if not self.queries:
            return 0.0
        return sum(getattr(q, attr) for q in self.queries) / len(self.queries)

    @property
    def recall(self) -> float:
        return self._avg("recall")

    @property
    def precision(self) -> float:
        return self._avg("precision")

    @property
    def mrr(self) -> float:
        return self._avg("mrr")

    @property
    def ndcg(self) -> float:
        return self._avg("ndcg")

    @property
    def latency_ms(self) -> float:
        return self._avg("latency_ms")


# -- 评测入口 ---------------------------------------------------------------
def build_chunks(path: str, company_name: str, fiscal_year: int, report_id: str = "bull-2024"):
    """解析 PDF + 结构切块，产出检索评测用的 EvidenceChunk。"""
    parsed = parse_pdf(path, company_name, fiscal_year, StatementType.BALANCE_SHEET)
    return chunk_pages(parsed.pages, company_name, fiscal_year, report_id)


def build_facts(path: str, company_name: str, fiscal_year: int) -> list[FinancialFact]:
    """解析三张报表，产出结构化财务事实（数值型查询的检索源）。

    数值查询横跨三表：存货/货币资金在资产负债表、营收/研发在利润表、
    经营现金流在现金流量表。只解析一张表会漏科目（评测 C 早期只解析资产负债表）。
    """
    facts: list[FinancialFact] = []
    for st in (StatementType.BALANCE_SHEET, StatementType.INCOME_STATEMENT, StatementType.CASH_FLOW):
        facts.extend(parse_pdf(path, company_name, fiscal_year, st).facts)
    return facts


def fact_recall_at_k(
    ranked_facts: list[FinancialFact], gold_item: str | None, gold_values: list[float], k: int
) -> float:
    """数值型查询的 Recall@K：每个目标数值是否被前 K 个事实命中（去重）。"""
    if not gold_values or gold_item is None:
        return 0.0
    hit_values = 0
    for gv in gold_values:
        if any(
            f.standard_item == gold_item and abs((f.value or 0.0) - gv) < 1e-6
            for f in ranked_facts[:k]
        ):
            hit_values += 1
    return hit_values / len(gold_values)


def eval_retrieval(
    chunks,
    queries: list[RetrievalQuery] | None = None,
    company_name: str = "公牛集团",
    fiscal_year: int = 2024,
    k: int = 5,
    embedder=None,
    reranker=None,
) -> RetrievalEvalResult:
    """对一组查询跑检索，计算 Recall@K / Precision@K / MRR / nDCG@K / Latency。

    embedder 为 None 时是纯 BM25 基线；传 BGEEmbedder 时是 BM25+BGE 混合召回；
    reranker 为 None 时不做重排，传 Reranker 时对候选池重排。三者用同一评测集对比
    （方案「不以回答流畅判断好坏」）。
    """
    queries = queries or BUILTIN_QUERIES
    retriever = Retriever(chunks, embedder=embedder, reranker=reranker)
    result = RetrievalEvalResult(k=k)

    for q in queries:
        gold = gold_chunk_ids(chunks, q.gold_phrases)
        t0 = time.perf_counter()
        ranked = [c.chunk_id for c in retriever.retrieve(q.query, company_name=company_name, fiscal_year=fiscal_year, top_k=k)]
        latency = (time.perf_counter() - t0) * 1000.0
        result.queries.append(
            QueryResult(
                qid=q.qid, query=q.query, n_gold=len(gold),
                ranked_ids=ranked, gold_ids=gold,
                recall=recall_at_k(ranked, gold, k),
                precision=precision_at_k(ranked, gold, k),
                mrr=mrr(ranked, gold),
                ndcg=ndcg_at_k(ranked, gold, k),
                latency_ms=latency,
            )
        )
    return result


def eval_fact_retrieval(
    facts: list[FinancialFact],
    queries: list[RetrievalQuery] | None = None,
    company_name: str = "公牛集团",
    fiscal_year: int = 2024,
    k: int = 5,
) -> RetrievalEvalResult:
    """数值型查询走结构化 facts 检索（FactRetriever），计算 Recall@K / Latency。"""
    queries = queries or BUILTIN_QUERIES
    retriever = FactRetriever(facts)
    result = RetrievalEvalResult(k=k)

    for q in queries:
        if q.kind != "fact":
            continue
        t0 = time.perf_counter()
        ranked = retriever.retrieve(q.query, company_name=company_name, fiscal_year=fiscal_year, top_k=k)
        latency = (time.perf_counter() - t0) * 1000.0
        result.queries.append(
            QueryResult(
                qid=q.qid, query=q.query, n_gold=len(q.gold_values),
                ranked_ids=[f.fact_id for f in ranked], gold_ids=set(),
                kind="fact",
                recall=fact_recall_at_k(ranked, q.gold_item, q.gold_values, k),
                latency_ms=latency,
            )
        )
    return result


def eval_retrieval_mixed(
    chunks: list,
    facts: list[FinancialFact],
    queries: list[RetrievalQuery] | None = None,
    company_name: str = "公牛集团",
    fiscal_year: int = 2024,
    k: int = 5,
    reranker=None,
) -> RetrievalEvalResult:
    """分路径检索评测：数值型走 facts、定性型走 chunk，合并计算综合 Recall@K。

    落实「财务事实由数据层提供」：数值查询的答案在 facts，文本证据负责定性/归因。
    综合 Recall 是所有查询（fact + chunk）Recall@K 的平均。
    """
    queries = queries or BUILTIN_QUERIES
    result = eval_fact_retrieval(facts, queries, company_name, fiscal_year, k)

    # chunk 型查询复用原 eval_retrieval 的检索逻辑
    chunk_queries = [q for q in queries if q.kind == "chunk"]
    chunk_result = eval_retrieval(chunks, chunk_queries, company_name, fiscal_year, k, reranker=reranker)
    result.queries.extend(chunk_result.queries)
    return result
