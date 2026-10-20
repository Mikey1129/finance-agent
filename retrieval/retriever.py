"""retrieval.retriever —— 混合检索（BM25 + BGE）+ 元数据过滤（方案 5.11）。

- 中文分词用字符 2-gram（无 jieba 依赖），英文按字母数字切词；
- BM25 为 Okapi 变体，纯 Python 实现（可替换为 rank-bm25，不改变接口）；
- BGE 语义召回由 embedding.py 提供，与 BM25 做 RRF 融合（embedder 缺失/失败降级纯 BM25）；
- 召回前按公司/年度/章节做元数据过滤——财报 RAG 极易召回「语义相似但非当前
  分析对象」的文本，不能只靠相似度（方案 5.11）。

仅做「检索」，证据组装与充分性检查由 agent/evidence.py 负责。
"""

from __future__ import annotations

import math
import re

import numpy as np

from domain import EvidenceChunk

_ASCII_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_CJK_RUN_RE = re.compile(r"[一-鿿]+")


def tokenize(text: str) -> list[str]:
    """分词：英文按字母数字切词，中文连续片段取 2-gram（单字保留）。"""
    tokens: list[str] = []
    for m in _ASCII_WORD_RE.findall(text):
        tokens.append(m.lower())
    for m in _CJK_RUN_RE.findall(text):
        if len(m) == 1:
            tokens.append(m)
        else:
            tokens.extend(m[i : i + 2] for i in range(len(m) - 1))
    return tokens


class BM25Index:
    """Okapi BM25 索引。docs 为分词后的文档列表。"""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.k1 = k1
        self.b = b
        self.n_docs = len(docs)
        self.avgdl = (sum(len(d) for d in docs) / self.n_docs) if self.n_docs else 0.0
        self.df: dict[str, int] = {}
        for d in docs:
            for t in set(d):
                self.df[t] = self.df.get(t, 0) + 1

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log((self.n_docs - df + 0.5) / (df + 0.5) + 1.0)

    def score(self, query_tokens: list[str], doc: list[str]) -> float:
        if not doc or self.avgdl == 0:
            return 0.0
        tf: dict[str, int] = {}
        for t in doc:
            tf[t] = tf.get(t, 0) + 1
        total = 0.0
        for t in query_tokens:
            if t not in tf:
                continue
            f = tf[t]
            norm = f + self.k1 * (1.0 - self.b + self.b * len(doc) / self.avgdl)
            total += self._idf(t) * f * (self.k1 + 1.0) / norm
        return total


def _match_meta(
    chunk: EvidenceChunk,
    company_name: str | None,
    fiscal_year: int | None,
    section: str | None,
) -> bool:
    if company_name and chunk.company_name != company_name:
        return False
    if fiscal_year is not None and chunk.fiscal_year != fiscal_year:
        return False
    if section and not (chunk.section_path and section in chunk.section_path):
        return False
    return True


_RRF_K = 60  # RRF 融合常数（业界常用默认）


def _rrf_fuse(ranked_a: list[int], ranked_b: list[int], top_k: int) -> list[int]:
    """Reciprocal Rank Fusion：按两路排名融合，得分 = Σ 1/(k + rank)。"""
    scores: dict[int, float] = {}
    for rank, idx in enumerate(ranked_a, start=1):
        scores[idx] = scores.get(idx, 0.0) + 1.0 / (_RRF_K + rank)
    for rank, idx in enumerate(ranked_b, start=1):
        scores[idx] = scores.get(idx, 0.0) + 1.0 / (_RRF_K + rank)
    return sorted(scores, key=lambda i: -scores[i])[:top_k]


class Retriever:
    """混合检索器：元数据过滤 → BM25 词法 + BGE 语义 → RRF 融合 → Reranker 重排 → top_k。

    embedder 为 None（或编码失败）时降级为纯 BM25，行为与基线一致。
    reranker 为 None（或打分失败）时保持召回原顺序，行为与接入前一致。
    方案 5.11「分别召回 → 合并去重 → 重排」。
    """

    def __init__(self, chunks: list[EvidenceChunk], embedder=None, cache=None, reranker=None):
        self.chunks = chunks
        self.embedder = embedder
        self.cache = cache
        self.reranker = reranker
        self._doc_tokens = [tokenize(c.text) for c in chunks]
        self._index = BM25Index(self._doc_tokens)
        self._doc_embeddings: np.ndarray | None = None  # 懒构建（首次向量召回时）

    def _filtered_indices(
        self, company_name: str | None, fiscal_year: int | None, section: str | None
    ) -> list[int]:
        return [
            i for i, c in enumerate(self.chunks)
            if _match_meta(c, company_name, fiscal_year, section)
        ]

    def _bm25_ranked(self, query_tokens: list[str], indices: list[int], top_n: int) -> list[int]:
        scored = [(self._index.score(query_tokens, self._doc_tokens[i]), i) for i in indices]
        scored = [(s, i) for s, i in scored if s > 0]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [i for _, i in scored[:top_n]]

    def _vec_ranked(self, query: str, indices: list[int], top_n: int) -> list[int]:
        """BGE 语义召回；embedder 不可用返回空列表（调用方降级纯 BM25）。"""
        if self.embedder is None or not indices:
            return []
        if self._doc_embeddings is None:
            self._build_doc_embeddings()
        if self._doc_embeddings is None:
            return []
        q = self.embedder.encode([query])
        if q is None:
            return []
        sims = self._doc_embeddings[indices] @ q[0]  # 归一化向量点积 = 余弦相似度
        order = np.argsort(-sims)
        return [indices[i] for i in order[:top_n]]

    def _build_doc_embeddings(self) -> None:
        if self.embedder is None:
            return
        vecs = self.embedder.encode([c.text for c in self.chunks])
        self._doc_embeddings = np.asarray(vecs, dtype="float32") if vecs is not None else None

    def _rerank(self, query: str, candidates: list[int]) -> list[int] | None:
        """对候选 chunk 用 reranker 重排；失败返回 None（调用方保持原顺序）。"""
        docs = [self.chunks[i].text for i in candidates]
        scores = self.reranker.rank(query, docs)
        if not scores or len(scores) != len(candidates):
            return None
        order = sorted(range(len(candidates)), key=lambda i: -scores[i])
        return [candidates[i] for i in order]

    def retrieve(
        self,
        query: str,
        company_name: str | None = None,
        fiscal_year: int | None = None,
        section: str | None = None,
        top_k: int = 5,
    ) -> list[EvidenceChunk]:
        """元数据过滤 → BM25(+BGE 语义) → RRF 融合 → 去重 → top_k 个片段。"""
        if self.cache is not None:
            cached = self.cache.get(query, company_name, fiscal_year, section, top_k)
            if cached is not None:
                return cached

        indices = self._filtered_indices(company_name, fiscal_year, section)
        if not indices:
            return []

        top_n = max(top_k * 4, 20)  # 候选池，保证 top_k 有足够来源（reranker 重排的输入）
        bm25_ranked = self._bm25_ranked(tokenize(query), indices, top_n)
        vec_ranked = self._vec_ranked(query, indices, top_n)

        # 候选池：有 BGE 时 RRF 融合，否则纯 BM25。保留 top_n（而非 top_k）供 reranker 重排。
        candidates = _rrf_fuse(bm25_ranked, vec_ranked, top_n) if vec_ranked else bm25_ranked

        # reranker 对候选池精细重排；打分失败降级为召回原顺序（行为与接入前一致）
        if self.reranker is not None and len(candidates) > 1:
            reranked = self._rerank(query, candidates)
            if reranked is not None:
                candidates = reranked

        ranked = candidates[:top_k]

        # 按 chunk_id 去重（保持排序，防御性处理重复 chunk）
        result: list[EvidenceChunk] = []
        seen: set[str] = set()
        for i in ranked:
            chunk = self.chunks[i]
            if chunk.chunk_id not in seen:
                seen.add(chunk.chunk_id)
                result.append(chunk)
        if self.cache is not None:
            self.cache.put(query, company_name, fiscal_year, section, top_k, result)
        return result
