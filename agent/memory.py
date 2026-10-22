"""agent.memory —— 会话状态 + 长期记忆（方案 5.6）。

三类存储（不可混为一谈）：
  1. 会话状态：LangGraph State + Checkpointer 保存（阶段 3 graph.py 接 Checkpointer）；
  2. 长期记忆：用户偏好、项目元数据、历史任务索引、经确认的可复用信息；
  3. 权威业务数据：带版本财务数据层（DuckDB）+ AnalysisRun。

本模块第一版用进程内 dict 实现长期记忆（MemoryStore）与会话历史（SessionStore），
阶段 4 再持久化。铁律：长期记忆必须有明确的读取/更新/失效/删除规则；记忆中的
财务相关信息必须重新核验来源与版本（MemoryItem.source 记录来源），不直接当作
未经验证的事实。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field


class MemoryItem(BaseModel):
    """一条长期记忆。kind 取值：preference / project / task_index / reusable / financial。"""

    key: str
    value: Any
    kind: str = "reusable"
    source: str | None = Field(default=None, description="来源引用（财务信息必须核验来源）")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    expires_at: datetime | None = Field(default=None, description="失效时间，None 表示永久")

    def expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        return self.expires_at is not None and now >= self.expires_at


class MemoryStore:
    """长期记忆：读取 / 更新 / 失效 / 删除（方案 5.6 铁律）。"""

    def __init__(self):
        self._items: dict[str, MemoryItem] = {}

    def set(
        self,
        key: str,
        value: Any,
        kind: str = "reusable",
        source: str | None = None,
        ttl_seconds: int | None = None,
    ) -> MemoryItem:
        """写入或覆盖一条记忆；ttl_seconds 指定过期时间。"""
        now = datetime.now()
        expires_at = now + timedelta(seconds=ttl_seconds) if ttl_seconds is not None else None
        item = MemoryItem(
            key=key, value=value, kind=kind, source=source,
            created_at=now, updated_at=now, expires_at=expires_at,
        )
        self._items[key] = item
        return item

    def get(self, key: str) -> Any | None:
        """读取记忆；已过期则自动删除并返回 None。"""
        item = self._items.get(key)
        if item is None:
            return None
        if item.expired():
            del self._items[key]
            return None
        return item.value

    def update(self, key: str, value: Any) -> bool:
        """更新已有记忆的值（不存在的 key 返回 False）。"""
        item = self._items.get(key)
        if item is None or item.expired():
            return False
        item.value = value
        item.updated_at = datetime.now()
        return True

    def delete(self, key: str) -> bool:
        """删除记忆。"""
        return self._items.pop(key, None) is not None

    def invalidate(self, key: str) -> bool:
        """失效（等同删除）。"""
        return self.delete(key)

    def cleanup_expired(self) -> int:
        """清理所有过期记忆，返回清理条数。"""
        expired_keys = [k for k, v in self._items.items() if v.expired()]
        for k in expired_keys:
            del self._items[k]
        return len(expired_keys)

    def search(self, kind: str | None = None) -> list[MemoryItem]:
        """按类型检索记忆（不过滤过期）。"""
        items = list(self._items.values())
        if kind is not None:
            items = [i for i in items if i.kind == kind]
        return items


class SqliteMemoryStore(MemoryStore):
    """长期记忆的 SQLite 持久化实现（阶段 4）。

    接口与进程内 MemoryStore 完全一致（set/get/update/delete/invalidate/
    cleanup_expired/search），但落盘到 SQLite，跨会话/跨重启保留。铁律不变：
    财务相关信息必须带来源、过期自动删除、删除/失效规则明确。
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS memory_items (
        key         TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        kind        TEXT NOT NULL,
        source      TEXT,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL,
        expires_at  TEXT
    );
    """

    def __init__(self, path: str = "storage/memory.db"):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        # 多 worker 并发：WAL 允许一写多读；busy_timeout 锁冲突时等待而非立即报错
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(self._SCHEMA)

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="microseconds")

    def _to_item(self, row: tuple) -> MemoryItem:
        key, value, kind, source, created_at, updated_at, expires_at = row
        return MemoryItem(
            key=key, value=json.loads(value), kind=kind, source=source,
            created_at=datetime.fromisoformat(created_at),
            updated_at=datetime.fromisoformat(updated_at),
            expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
        )

    def set(
        self,
        key: str,
        value: Any,
        kind: str = "reusable",
        source: str | None = None,
        ttl_seconds: int | None = None,
    ) -> MemoryItem:
        now = datetime.now()
        expires_at = now + timedelta(seconds=ttl_seconds) if ttl_seconds is not None else None
        item = MemoryItem(
            key=key, value=value, kind=kind, source=source,
            created_at=now, updated_at=now, expires_at=expires_at,
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO memory_items "
            "(key, value, kind, source, created_at, updated_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                key, json.dumps(value, ensure_ascii=False), kind, source,
                now.isoformat(timespec="microseconds"),
                now.isoformat(timespec="microseconds"),
                expires_at.isoformat(timespec="microseconds") if expires_at else None,
            ),
        )
        self.conn.commit()
        return item

    def get(self, key: str) -> Any | None:
        row = self.conn.execute(
            "SELECT key, value, kind, source, created_at, updated_at, expires_at "
            "FROM memory_items WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        item = self._to_item(row)
        if item.expired():
            self.delete(key)
            return None
        return item.value

    def update(self, key: str, value: Any) -> bool:
        if self.get(key) is None:
            return False
        self.conn.execute(
            "UPDATE memory_items SET value = ?, updated_at = ? WHERE key = ?",
            (json.dumps(value, ensure_ascii=False), self._now(), key),
        )
        self.conn.commit()
        return True

    def delete(self, key: str) -> bool:
        cur = self.conn.execute("DELETE FROM memory_items WHERE key = ?", (key,))
        self.conn.commit()
        return cur.rowcount > 0

    def invalidate(self, key: str) -> bool:
        return self.delete(key)

    def cleanup_expired(self) -> int:
        cur = self.conn.execute(
            "DELETE FROM memory_items WHERE expires_at IS NOT NULL AND expires_at < ?",
            (self._now(),),
        )
        self.conn.commit()
        return cur.rowcount

    def search(self, kind: str | None = None) -> list[MemoryItem]:
        self.cleanup_expired()
        if kind is None:
            rows = self.conn.execute(
                "SELECT key, value, kind, source, created_at, updated_at, expires_at "
                "FROM memory_items ORDER BY updated_at DESC"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT key, value, kind, source, created_at, updated_at, expires_at "
                "FROM memory_items WHERE kind = ? ORDER BY updated_at DESC",
                (kind,),
            ).fetchall()
        return [self._to_item(r) for r in rows]


class SessionStore:
    """会话状态：thread_id → 最近对话历史（供 ContextResolver/Builder 使用）。"""

    def __init__(self):
        self._sessions: dict[str, list[str]] = {}

    def append(self, thread_id: str, message: str) -> None:
        self._sessions.setdefault(thread_id, []).append(message)

    def get_history(self, thread_id: str, max_turns: int | None = None) -> list[str]:
        history = self._sessions.get(thread_id, [])
        if max_turns is not None:
            return history[-max_turns:]
        return list(history)

    def clear(self, thread_id: str) -> None:
        self._sessions.pop(thread_id, None)
