"""agent.checkpoint —— 会话状态持久化（方案 5.5 / 5.6 的 Checkpointer）。

Checkpointer 负责「状态持久化恢复」：把一次任务完成后的会话快照落盘，供多轮
对话续接（下次提问继承公司/期间/意图等）。与另外三类存储严格分离：
  - AgentState：当前任务内存态（agent/state.py）
  - MemoryStore：跨会话长期记忆（agent/memory.py）
  - DuckDB + AnalysisRun：权威业务数据 + 运行记录（finance/store.py）

铁律（方案 5.6）：
  1. 有过期/清理/删除规则，不无限期保留；
  2. 敏感信息规则——只存引用与摘要（company_name/periods/intent/来源引用），
     绝不存财报全文或大段文本；
  3. Checkpointer 不能替代长期记忆、财务库或审计日志。

实现用 SQLite（stdlib），与 DuckDB 业务数据物理分离，接口对齐
save / load / list_threads / delete / cleanup_expired。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id  TEXT PRIMARY KEY,
    thread_id      TEXT NOT NULL,
    state_json     TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    expires_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_thread ON checkpoints(thread_id);
"""

# 允许写入 checkpoint 的字段白名单（敏感信息规则：只存引用/摘要，不存全文）
_ALLOWED_KEYS = {
    "company_name",
    "periods",
    "intent",
    "known_companies",
    "evidence_refs",
    "metric_codes",
    "standard_items",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="microseconds")


class Checkpointer:
    """SQLite 持久化的会话状态快照。path=':memory:' 为进程内，不跨重启。"""

    def __init__(self, path: str = ":memory:", ttl_seconds: int | None = None):
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(_SCHEMA)

    def save(self, thread_id: str, state: dict[str, Any]) -> str:
        """保存会话快照，返回 checkpoint_id。只持久化白名单字段（敏感信息规则）。"""
        filtered = {
            k: v for k, v in state.items() if k in _ALLOWED_KEYS and v not in (None, "", [])
        }
        checkpoint_id = f"{thread_id}-{uuid.uuid4().hex[:8]}"
        expires = None
        if self.ttl_seconds is not None:
            expires = (
                datetime.now() + timedelta(seconds=self.ttl_seconds)
            ).isoformat(timespec="microseconds")
        self.conn.execute(
            "INSERT INTO checkpoints (checkpoint_id, thread_id, state_json, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (checkpoint_id, thread_id, json.dumps(filtered, ensure_ascii=False), _now(), expires),
        )
        self.conn.commit()
        return checkpoint_id

    def load(self, thread_id: str) -> dict[str, Any] | None:
        """读取该线程最近一次未过期快照；过期自动清理。"""
        self.cleanup_expired()
        row = self.conn.execute(
            "SELECT state_json FROM checkpoints WHERE thread_id = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def list_threads(self) -> list[str]:
        self.cleanup_expired()
        rows = self.conn.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall()
        return [r[0] for r in rows]

    def delete(self, thread_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def cleanup_expired(self) -> int:
        cur = self.conn.execute(
            "DELETE FROM checkpoints WHERE expires_at IS NOT NULL AND expires_at < ?",
            (_now(),),
        )
        self.conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self.conn.close()
