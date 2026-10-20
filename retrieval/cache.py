"""retrieval.cache —— 检索结果缓存（阶段 5「缓存 / 上下文压缩」）。

TTL + 容量上限（满时淘汰最早到期条目）。缓存检索结果避免重复 BM25 / 向量计算。
默认关闭（Retriever.cache=None），启用时按 (query, 过滤条件, top_k) 精确命中；
命中返回浅拷贝，避免调用方修改污染缓存。ttl_seconds<=0 表示写入后立即过期。
"""

from __future__ import annotations

import time
from typing import Any


class RetrievalCache:
    """检索结果的内存缓存（进程内，单调时钟计 TTL）。"""

    def __init__(self, max_size: int = 128, ttl_seconds: float = 300.0):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._data: dict[tuple, tuple[float, list]] = {}

    @staticmethod
    def _key(
        query: str,
        company_name: str | None,
        fiscal_year: int | None,
        section: str | None,
        top_k: int,
    ) -> tuple:
        return (query, company_name, fiscal_year, section, top_k)

    def get(
        self,
        query: str,
        company_name: str | None = None,
        fiscal_year: int | None = None,
        section: str | None = None,
        top_k: int = 5,
    ) -> list | None:
        """命中返回浅拷贝；未命中或已过期返回 None。"""
        key = self._key(query, company_name, fiscal_year, section, top_k)
        item = self._data.get(key)
        if item is None:
            return None
        expire_ts, result = item
        if time.monotonic() >= expire_ts:
            del self._data[key]
            return None
        return list(result)

    def put(
        self,
        query: str,
        company_name: str | None = None,
        fiscal_year: int | None = None,
        section: str | None = None,
        top_k: int = 5,
        result: list[Any] | None = None,
    ) -> None:
        """写入缓存；满容量时淘汰最旧（最早到期）条目。"""
        if len(self._data) >= self.max_size:
            oldest = min(self._data, key=lambda k: self._data[k][0])
            del self._data[oldest]
        key = self._key(query, company_name, fiscal_year, section, top_k)
        self._data[key] = (time.monotonic() + self.ttl_seconds, list(result or []))

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
