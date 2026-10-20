"""retrieval.reranker —— 候选重排（方案 5.11 ④「合并、去重与重排序」）。

Reranker 是 cross-encoder 式精细打分：对 (query, doc) 对计算相关度，专门纠正双塔
embedding「语义沾边但非当前科目」挤进 top-k 的问题——这正是 BGE 语义召回劣化的根源
（README 阶段 4 已实测：混合召回 Recall 0.632→0.325）。

本项目走 DashScope 文本排序 API（qwen3-rerank），不引入本地重模型：
- Docker 镜像特意排除 torch（README 阶段 5），本地 reranker 会破坏轻量镜像；
- API 免本地 GPU/内存，与 DeepSeek 直连（agent/llm.py）同一条技术路线。

设计要点（与 embedding.py 一致）：
- key 从环境变量 DASHSCOPE_API_KEY 读取（不硬编码、不入库）；
- 缺 key / 请求失败 / 空结果返回 None，调用方降级为召回原顺序，绝不因 reranker 不可用崩溃；
- rank_fn 可注入（测试 mock 不触网）。
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

# DashScope 文本排序原生接口（非 compatible-mode，后者只覆盖 LLM/Embedding）。
_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"

# 默认用 qwen3-rerank。注意：gte-rerank / gte-rerank-v2 已于 2026-05-30 下线，
# 官方推荐替代为 qwen3-rerank（多语言、单文档 4000 Token、最多 500 文档）。
# 端点实测（无 key 时）为 401 而非 404，证明原生嵌套端点 + qwen3-rerank 可用。
_DEFAULT_MODEL = "qwen3-rerank"


class Reranker(ABC):
    """重排器抽象：对候选文档打分。"""

    @abstractmethod
    def rank(self, query: str, documents: list[str]) -> list[float] | None:
        """返回与 documents 等长的相关度分数（越高越相关）；失败返回 None（降级）。"""


class DashScopeReranker(Reranker):
    """DashScope 文本排序（qwen3-rerank）。rank_fn 可注入（测试 mock）。"""

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        api_key: str | None = None,
        timeout: float = 30.0,
        rank_fn=None,
    ):
        self.model = model
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        self.timeout = timeout
        self._rank_fn = rank_fn  # 测试注入：callable(query, list[str]) -> list[float] | None

    def rank(self, query: str, documents: list[str]) -> list[float] | None:
        if not documents:
            return []
        if self._rank_fn is not None:
            return self._rank_fn(query, list(documents))
        if not self.api_key:
            return None
        try:
            import httpx

            resp = httpx.post(
                _URL,
                json={
                    "model": self.model,
                    "input": {"query": query, "documents": list(documents)},
                    "parameters": {"return_documents": False, "top_n": len(documents)},
                },
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            body = resp.json()
            results = body.get("output", {}).get("results") or []
        except Exception:
            # 网络不可达 / 超时 / 鉴权失败 / 非 JSON → 降级，调用方按召回原顺序返回
            return None
        if not results:
            return None
        # 把按 index 命中的分数映射回与 documents 等长的数组；未命中位置记 0 分
        scores = [0.0] * len(documents)
        for r in results:
            idx = r.get("index")
            if isinstance(idx, int) and 0 <= idx < len(scores):
                scores[idx] = float(r.get("relevance_score", 0.0))
        return scores


def build_reranker() -> Reranker | None:
    """有 DASHSCOPE_API_KEY 时返回可用的 DashScopeReranker，否则 None（降级纯 BM25）。

    评测结论（公牛集团 2024 年报，606 chunk，k=5）：BM25 + qwen3-rerank 较纯 BM25
    Recall@5 0.632→0.740、nDCG@5 0.534→0.663，有数据证明提升，故工具层默认启用；
    缺 key / 请求失败时自动降级为纯 BM25，行为与接入前一致。
    """
    if os.getenv("DASHSCOPE_API_KEY"):
        return DashScopeReranker()
    return None
