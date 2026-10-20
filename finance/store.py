"""store —— DuckDB 存储与查询层。

四张表：report_docs / financial_facts / provenance / evidence_chunks。
DuckDB 是权威业务数据（+ AnalysisRun），Checkpointer 不能替代它（方案 5.5/5.6）。
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

import duckdb

from domain import FinancialFact, Provenance, ReportDoc

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
"""


def _val(v: Any) -> Any:
    """枚举 -> 值；datetime -> isoformat。"""
    if isinstance(v, Enum):
        return v.value
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


class Store:
    """DuckDB 存储。path 传 ':memory:' 为内存库，传文件路径为持久库。"""

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.conn = duckdb.connect(path)
        self.conn.execute(_SCHEMA_SQL)

    # -- 写入 ---------------------------------------------------------------
    def insert_report_doc(self, doc: ReportDoc) -> None:
        d = doc.model_dump()
        cols = list(d.keys())
        placeholders = ", ".join("?" for _ in cols)
        values = [_val(d[c]) for c in cols]
        self.conn.execute(
            f"INSERT INTO report_docs ({', '.join(cols)}) VALUES ({placeholders})",
            values,
        )

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
        self.conn.executemany(
            f"INSERT INTO financial_facts ({', '.join(cols)}) VALUES ({placeholders})",
            rows,
        )

    def insert_provenance(self, prov: Provenance) -> None:
        d = prov.model_dump()
        d["transform_steps"] = json.dumps(d.get("transform_steps", []), ensure_ascii=False)
        cols = list(d.keys())
        placeholders = ", ".join("?" for _ in cols)
        values = [_val(d[c]) for c in cols]
        self.conn.execute(
            f"INSERT INTO provenance ({', '.join(cols)}) VALUES ({placeholders})",
            values,
        )

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
        return self._rows_as_dicts(sql, params)

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
        rows = self._rows_as_dicts(
            "SELECT * FROM financial_facts WHERE fact_id = ?", [fact_id]
        )
        return rows[0] if rows else None

    def _rows_as_dicts(self, sql: str, params: list[Any]) -> list[dict]:
        """执行查询并把结果转为 dict 列表（按列名对齐，避免位置索引出错）。"""
        rows = self.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def count_facts(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM financial_facts").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
