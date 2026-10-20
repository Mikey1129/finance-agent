"""retrieval.retriever —— BM25 基线检索 + 元数据过滤（方案 5.11）。

第一版 BM25 基线（BGE 语义召回与 Reranker 阶段 3/4 接入）：
- 中文分词用字符 2-gram（无 jieba 依赖），英文按字母数字切词；
- BM25 为 Okapi 变体，纯 Python 实现（可替换为 rank-bm25，不改变接口）；
- 召回前按公司/年度/章节做元数据过滤——财报 RAG 极易召回「语义相似但非当前
  分析对象」的文本，不能只靠相似度（方案 5.11）。

仅做「检索」，证据组装与充分性检查由 agent/evidence.py 负责。
"""

from __future__ import annotations

import math
import re

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


class Retriever:
    """BM25 基线检索器：元数据过滤 → BM25 打分 → 排序 → 去重 → top_k。"""

    def __init__(self, chunks: list[EvidenceChunk]):
        self.chunks = chunks
        self._doc_tokens = [tokenize(c.text) for c in chunks]
        self._index = BM25Index(self._doc_tokens)

    def retrieve(
        self,
        query: str,
        company_name: str | None = None,
        fiscal_year: int | None = None,
        section: str | None = None,
        top_k: int = 5,
    ) -> list[EvidenceChunk]:
        """按元数据过滤后 BM25 排序，返回去重后的 top_k 个片段。"""
        query_tokens = tokenize(query)
        scored: list[tuple[float, int]] = []
        for i, chunk in enumerate(self.chunks):
            if not _match_meta(chunk, company_name, fiscal_year, section):
                continue
            s = self._index.score(query_tokens, self._doc_tokens[i])
            if s > 0:
                scored.append((s, i))
        scored.sort(key=lambda x: x[0], reverse=True)

        # 按 chunk_id 去重（保持排序，防御性处理重复 chunk）
        result: list[EvidenceChunk] = []
        seen: set[str] = set()
        for _, i in scored[:top_k]:
            chunk = self.chunks[i]
            if chunk.chunk_id not in seen:
                seen.add(chunk.chunk_id)
                result.append(chunk)
        return result
