"""auth.audit —— 审计日志（阶段 5 企业级基础）。

记录「谁在何时对什么资源做了什么、结果如何」，与 AnalysisRun 分工不同：
- AnalysisRun（DuckDB）：一次分析「怎么算出来」的执行记录（可回放）；
- AuditLog（SQLite）：安全审计轨迹「谁访问/操作了什么」（who/when/what/result）。

SQLite 持久化，接口对齐 append / query / count。路径 ':memory:' 供测试用。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    event_id      TEXT PRIMARY KEY,
    user_id       TEXT,
    action        TEXT NOT NULL,
    resource_type TEXT,
    resource_id   TEXT,
    result        TEXT NOT NULL,
    detail        TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_events(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events(action);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="microseconds")


class AuditEvent(BaseModel):
    """一条审计事件。action 取值：ingest / ask / tool_call / access_denied /
    memory_set / memory_delete / export_run 等。"""

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    user_id: str | None = Field(default=None, description="触发用户；None=系统/未认证")
    action: str = Field(description="操作类型")
    resource_type: str | None = Field(default=None, description="资源类型 company/report/fact/tool")
    resource_id: str | None = Field(default=None, description="资源标识，如公司名/tool 名")
    result: str = Field(default="success", description="allowed/denied/success/error")
    detail: str | None = Field(default=None, description="补充说明")
    created_at: datetime = Field(default_factory=datetime.now)


class AuditLog:
    """SQLite 持久化审计日志。path=':memory:' 为进程内，不跨重启。"""

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(_SCHEMA)

    def append(self, event: AuditEvent) -> AuditEvent:
        """写入一条审计事件，返回带 event_id 的事件。"""
        self.conn.execute(
            "INSERT INTO audit_events "
            "(event_id, user_id, action, resource_type, resource_id, result, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id, event.user_id, event.action, event.resource_type,
                event.resource_id, event.result, event.detail,
                event.created_at.isoformat(timespec="microseconds"),
            ),
        )
        self.conn.commit()
        return event

    def record(
        self,
        action: str,
        user_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        result: str = "success",
        detail: str | None = None,
    ) -> AuditEvent:
        """快捷记录一条审计事件。"""
        return self.append(
            AuditEvent(
                user_id=user_id, action=action, resource_type=resource_type,
                resource_id=resource_id, result=result, detail=detail,
            )
        )

    def query(
        self,
        user_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """按用户 / 操作过滤审计事件，按时间倒序返回。"""
        sql = "SELECT * FROM audit_events WHERE 1=1"
        params: list = []
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        if action is not None:
            sql += " AND action = ?"
            params.append(action)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cur = self.conn.execute(sql, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [AuditEvent.model_validate(dict(zip(cols, r))) for r in rows]

    def count(self, user_id: str | None = None, action: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM audit_events WHERE 1=1"
        params: list = []
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        if action is not None:
            sql += " AND action = ?"
            params.append(action)
        return self.conn.execute(sql, params).fetchone()[0]

    def close(self) -> None:
        self.conn.close()
