"""store —— DuckDB 存储与查询层。

四张表：report_docs / financial_facts / provenance / evidence_chunks。
DuckDB 是权威业务数据（+ AnalysisRun），Checkpointer 不能替代它（方案 5.5/5.6）。

并发模型（多 worker 部署关键）：
- ':memory:'（测试/进程内）：常驻单连接，读写同库；
- 文件路径（生产）：短连接——查询开 read_only 连接（多进程并发读），写入用
  文件锁 + 短读写连接（DuckDB 单写者，写入极低频，串行化可接受）。
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

import duckdb

try:
    import fcntl
except ImportError:  # Windows 无 fcntl，回退进程内锁（本地单进程足够）
    fcntl = None

from domain import AnalysisRun, FinancialFact, Provenance, ReportDoc

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS report_docs (
    report_id            VARCHAR PRIMARY KEY,
    company_name         VARCHAR,
    fiscal_year          INTEGER,
    accounting_standard  VARCHAR,
    currency             VARCHAR,
    report_type          VARCHAR,
    consolidation_scope  VARCHAR,
    source_file          VARCHAR,
    file_hash            VARCHAR,
    version              INTEGER,
    parse_status         VARCHAR,
    created_at           VARCHAR,
    updated_at           VARCHAR
);

CREATE TABLE IF NOT EXISTS financial_facts (
    fact_id              VARCHAR PRIMARY KEY,
    report_id            VARCHAR,
    company_name         VARCHAR,
    fiscal_year          INTEGER,
    raw_item             VARCHAR,
    standard_item        VARCHAR,
    value                DOUBLE,
    raw_value            DOUBLE,
    unit                 VARCHAR,
    currency             VARCHAR,
    period               VARCHAR,
    period_type          VARCHAR,
    statement_type       VARCHAR,
    consolidation_scope  VARCHAR,
    data_status          VARCHAR,
    version              INTEGER,
    mapping_rule_version VARCHAR,
    is_derived           BOOLEAN,
    provenance_id        VARCHAR
);

CREATE TABLE IF NOT EXISTS provenance (
    provenance_id        VARCHAR PRIMARY KEY,
    report_id            VARCHAR,
    source_file          VARCHAR,
    page                 INTEGER,
    table_name           VARCHAR,
    cell                 VARCHAR,
    row_index            INTEGER,
    extraction_method    VARCHAR,
    mapping_rule_version VARCHAR,
    transform_steps      VARCHAR
);

CREATE TABLE IF NOT EXISTS evidence_chunks (
    chunk_id        VARCHAR PRIMARY KEY,
    report_id       VARCHAR,
    company_name    VARCHAR,
    fiscal_year     INTEGER,
    section_path    VARCHAR,
    page_start      INTEGER,
    page_end        INTEGER,
    parent_chunk_id VARCHAR,
    chunk_type      VARCHAR,
    source_span     VARCHAR,
    text            VARCHAR
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id             VARCHAR PRIMARY KEY,
    thread_id          VARCHAR,
    user_id            VARCHAR,
    user_query         VARCHAR,
    company_name       VARCHAR,
    periods            VARCHAR,
    intent             VARCHAR,
    status             VARCHAR,
    plan               VARCHAR,
    tool_calls         VARCHAR,
    validation_summary VARCHAR,
    final_answer       VARCHAR,
    data_version       VARCHAR,
    rule_version       VARCHAR,
    model_version      VARCHAR,
    prompt_version     VARCHAR,
    created_at         VARCHAR,
    finished_at        VARCHAR
);
"""


def _val(v: Any) -> Any:
    """枚举 -> 值；datetime -> isoformat。"""
    if isinstance(v, Enum):
        return v.value
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


class _FileLock:
    """跨进程文件锁。Linux 用 fcntl.flock（多 worker 互斥）；Windows 回退进程内锁。"""

    def __init__(self, lock_path: str):
        self._path = lock_path
        self._fd = None
        self._thread_lock = None
        if fcntl is None:
            import threading

            self._thread_lock = threading.Lock()

    def __enter__(self):
        if fcntl is not None:
            self._fd = open(self._path, "a")
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        else:
            self._thread_lock.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        if fcntl is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
        else:
            self._thread_lock.release()
        return False


class Store:
    """DuckDB 存储。path 传 ':memory:' 为内存库，传文件路径为持久库。"""

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._memory = path == ":memory:"
        if self._memory:
            self._conn = duckdb.connect(path)
            self._conn.execute(_SCHEMA_SQL)
        else:
            self._lock = _FileLock(path + ".lock")
            # 首次建表：写连接 + 文件锁（并发启动时也只会有一个进程建表）
            with self._lock:
                conn = duckdb.connect(path)
                conn.execute(_SCHEMA_SQL)
                conn.close()

    # -- 连接管理 -----------------------------------------------------------
    def _read(self, fn):
        """在只读连接上执行查询。memory 库用常驻连接，文件库用短只读连接。"""
        if self._memory:
            return fn(self._conn)
        conn = duckdb.connect(self.path, read_only=True)
        try:
            return fn(conn)
        finally:
            conn.close()

    def _write(self, fn):
        """在写连接上执行写入。文件库用文件锁 + 短写连接（串行化低频写入）。"""
        if self._memory:
            fn(self._conn)
            return
        with self._lock:
            conn = duckdb.connect(self.path)
            try:
                fn(conn)
            finally:
                conn.close()

    # -- 写入 ---------------------------------------------------------------
    def insert_report_doc(self, doc: ReportDoc) -> None:
        d = doc.model_dump()
        cols = list(d.keys())
        placeholders = ", ".join("?" for _ in cols)
        values = [_val(d[c]) for c in cols]

        def _do(conn):
            conn.execute(
                f"INSERT INTO report_docs ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )

        self._write(_do)

    def insert_financial_facts(self, facts: list[FinancialFact]) -> None:
        """批量插入财务事实。"""
        if not facts:
            return
        cols = list(FinancialFact.model_fields.keys())
        placeholders = ", ".join("?" for _ in cols)
        rows = []
        for f in facts:
            d = f.model_dump()
            rows.append([_val(d[c]) for c in cols])

        def _do(conn):
            conn.executemany(
                f"INSERT INTO financial_facts ({', '.join(cols)}) VALUES ({placeholders})",
                rows,
            )

        self._write(_do)

    def insert_provenance(self, prov: Provenance) -> None:
        d = prov.model_dump()
        d["transform_steps"] = json.dumps(d.get("transform_steps", []), ensure_ascii=False)
        cols = list(d.keys())
        placeholders = ", ".join("?" for _ in cols)
        values = [_val(d[c]) for c in cols]

        def _do(conn):
            conn.execute(
                f"INSERT INTO provenance ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )

        self._write(_do)

    def insert_analysis_run(self, run: AnalysisRun) -> None:
        """写入一条分析运行记录（可回放审计，方案 5.5）。"""
        d = run.model_dump()
        for col in ("periods", "plan", "tool_calls", "validation_summary"):
            d[col] = json.dumps(d.get(col, []), ensure_ascii=False)
        cols = list(d.keys())
        placeholders = ", ".join("?" for _ in cols)
        values = [_val(d[c]) for c in cols]

        def _do(conn):
            conn.execute(
                f"INSERT INTO analysis_runs ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )

        self._write(_do)

    # -- 查询 ---------------------------------------------------------------
    def query_facts(
        self,
        company_name: str | None = None,
        fiscal_year: int | None = None,
        standard_item: str | None = None,
        statement_type: str | None = None,
        report_id: str | None = None,
    ) -> list[dict]:
        """按条件查询财务事实（返回 dict 列表）。"""
        sql = "SELECT * FROM financial_facts WHERE 1=1"
        params: list[Any] = []
        if company_name is not None:
            sql += " AND company_name = ?"
            params.append(company_name)
        if fiscal_year is not None:
            sql += " AND fiscal_year = ?"
            params.append(fiscal_year)
        if standard_item is not None:
            sql += " AND standard_item = ?"
            params.append(standard_item)
        if statement_type is not None:
            sql += " AND statement_type = ?"
            params.append(statement_type)
        if report_id is not None:
            sql += " AND report_id = ?"
            params.append(report_id)
        return self._read(lambda conn: self._rows_as_dicts(conn, sql, params))

    def query_fact_values(
        self,
        company_name: str | None = None,
        fiscal_year: int | None = None,
        standard_item: str | None = None,
        statement_type: str | None = None,
    ) -> list[tuple[str, float | None]]:
        """返回 (fact_id, value) 列表，用于指标计算。"""
        rows = self.query_facts(company_name, fiscal_year, standard_item, statement_type)
        return [(r["fact_id"], r["value"]) for r in rows]

    def get_fact(self, fact_id: str) -> dict | None:
        def _do(conn):
            rows = self._rows_as_dicts(
                conn, "SELECT * FROM financial_facts WHERE fact_id = ?", [fact_id]
            )
            return rows[0] if rows else None

        return self._read(_do)

    def list_companies(self) -> list[str]:
        """已入库公司名（去重，供前端下拉与意图消歧）。"""

        def _do(conn):
            rows = conn.execute(
                "SELECT DISTINCT company_name FROM financial_facts ORDER BY company_name"
            ).fetchall()
            return [r[0] for r in rows if r[0]]

        return self._read(_do)

    def _rows_as_dicts(self, conn, sql: str, params: list[Any]) -> list[dict]:
        """执行查询并把结果转为 dict 列表（按列名对齐，避免位置索引出错）。"""
        rows = conn.execute(sql, params).fetchall()
        cols = [d[0] for d in conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def count_facts(self) -> int:
        return self._read(
            lambda conn: conn.execute("SELECT COUNT(*) FROM financial_facts").fetchone()[0]
        )

    def get_analysis_run(self, run_id: str) -> dict | None:
        def _do(conn):
            rows = self._rows_as_dicts(
                conn, "SELECT * FROM analysis_runs WHERE run_id = ?", [run_id]
            )
            return self._decode_run(rows[0]) if rows else None

        return self._read(_do)

    def query_analysis_runs(
        self,
        thread_id: str | None = None,
        company_name: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        sql = "SELECT * FROM analysis_runs WHERE 1=1"
        params: list[Any] = []
        if thread_id is not None:
            sql += " AND thread_id = ?"
            params.append(thread_id)
        if company_name is not None:
            sql += " AND company_name = ?"
            params.append(company_name)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        def _do(conn):
            return [self._decode_run(r) for r in self._rows_as_dicts(conn, sql, params)]

        return self._read(_do)

    def count_analysis_runs(self) -> int:
        return self._read(
            lambda conn: conn.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0]
        )

    @staticmethod
    def _decode_run(row: dict) -> dict:
        """把 JSON 字符串字段还原为 list/dict。"""
        for col in ("periods", "plan", "tool_calls", "validation_summary"):
            row[col] = json.loads(row[col]) if row[col] else []
        return row

    def close(self) -> None:
        if self._memory:
            self._conn.close()
