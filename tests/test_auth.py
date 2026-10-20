"""auth 权限控制与审计日志测试（阶段 5 企业增强）。

覆盖：AccessPolicy 角色/公司范围隔离、AuditLog 持久化、PermissionHook 工具入口层
校验、FinanceService 上传写权限、AgentGraph 提问越权阻止 + access_denied 审计。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent.graph import AgentGraph
from agent.hooks import HookManager, PermissionHook
from agent.tools import ToolStatus, build_tools
from app.service import FinanceService
from auth import AccessPolicy, AuditLog, Role, User
from domain import FinancialFact, StatementType
from finance.store import Store

_BALANCE_CSV = (
    "项目,2025年12月31日,2024年12月31日\n"
    "货币资金,5000,4000\n"
    "资产总计,10000,8000\n"
    "负债合计,6000,5000\n"
    "所有者权益合计,4000,3000\n"
)


def _admin() -> User:
    return User(user_id="u-admin", name="管理员", role=Role.ADMIN)


def _analyst() -> User:
    return User(user_id="u-analyst", name="分析师", role=Role.ANALYST, company_scope=["甲公司"])


def _viewer() -> User:
    return User(user_id="u-viewer", name="访客", role=Role.VIEWER, company_scope=["甲公司"])


def _store_with_facts() -> Store:
    s = Store(":memory:")
    s.insert_financial_facts(
        [
            FinancialFact(
                fact_id="f1", report_id="r1", company_name="甲公司", fiscal_year=2024,
                raw_item="revenue", standard_item="revenue", value=100.0,
                statement_type=StatementType.INCOME_STATEMENT, period="2024",
            )
        ]
    )
    return s


def _csv_file() -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
    f.write(_BALANCE_CSV)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# AccessPolicy
# ---------------------------------------------------------------------------
def test_access_policy_admin_full_access():
    p = AccessPolicy()
    assert p.can_read(_admin(), "甲公司") and p.can_read(_admin(), "乙丙公司")
    assert p.can_write(_admin(), "乙丙公司")


def test_access_policy_analyst_scoped():
    p = AccessPolicy()
    assert p.can_read(_analyst(), "甲公司")
    assert not p.can_read(_analyst(), "乙丙公司")
    assert p.can_write(_analyst(), "甲公司")
    assert not p.can_write(_analyst(), "乙丙公司")


def test_access_policy_viewer_read_only():
    p = AccessPolicy()
    assert p.can_read(_viewer(), "甲公司")
    assert not p.can_write(_viewer(), "甲公司")  # VIEWER 只读


def test_access_policy_no_user_is_open():
    """user=None 视为未启用权限，放行（向后兼容）。"""
    p = AccessPolicy()
    assert p.can_read(None, "乙丙公司")
    assert p.can_write(None, "乙丙公司")


def test_access_policy_scope_none_means_all():
    u = User(user_id="u", role=Role.ANALYST, company_scope=None)
    assert AccessPolicy().can_read(u, "任意公司")
    assert AccessPolicy().can_write(u, "任意公司")


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------
def test_audit_log_append_and_query():
    log = AuditLog(":memory:")
    log.record("ingest", user_id="u1", resource_type="company", resource_id="甲公司")
    log.record("access_denied", user_id="u1", resource_type="company", resource_id="乙丙公司", result="denied")
    assert log.count() == 2
    denied = log.query(action="access_denied")
    assert len(denied) == 1 and denied[0].resource_id == "乙丙公司" and denied[0].result == "denied"
    assert log.count(user_id="u1") == 2
    assert log.count(user_id="u2") == 0


# ---------------------------------------------------------------------------
# PermissionHook
# ---------------------------------------------------------------------------
def test_permission_hook_blocks_out_of_scope():
    hm = HookManager([PermissionHook()])
    r = hm.invoke(build_tools(_store_with_facts()), "query_financial_fact", user=_analyst(), company_name="乙丙公司")
    assert r.status is ToolStatus.ERROR
    assert r.error.type == "hook_blocked"
    assert "无权访问" in r.error.message


def test_permission_hook_allows_in_scope():
    hm = HookManager([PermissionHook()])
    r = hm.invoke(build_tools(_store_with_facts()), "query_financial_fact", user=_analyst(), company_name="甲公司")
    assert r.status is ToolStatus.SUCCESS


def test_permission_hook_no_user_is_open():
    hm = HookManager([PermissionHook()])
    r = hm.invoke(build_tools(_store_with_facts()), "query_financial_fact", company_name="甲公司")
    assert r.status is ToolStatus.SUCCESS


# ---------------------------------------------------------------------------
# FinanceService 权限接入
# ---------------------------------------------------------------------------
def _service() -> FinanceService:
    return FinanceService(":memory:", ":memory:", ":memory:", ":memory:")


def test_service_ingest_viewer_denied():
    svc = _service()
    path = _csv_file()
    with pytest.raises(PermissionError):
        svc.ingest(path, "甲公司", 2025, user=_viewer())  # VIEWER 只读，不可上传
    assert svc.audit_log.count(action="access_denied") == 1
    Path(path).unlink(missing_ok=True)


def test_service_ingest_analyst_allowed():
    svc = _service()
    path = _csv_file()
    result = svc.ingest(path, "甲公司", 2025, user=_analyst())
    assert result["company_name"] == "甲公司"
    assert svc.audit_log.count(action="ingest") == 1
    Path(path).unlink(missing_ok=True)


def test_service_ingest_no_user_is_open():
    svc = _service()
    path = _csv_file()
    result = svc.ingest(path, "甲公司", 2025)  # 未传 user 行为不变
    assert result["company_name"] == "甲公司"
    assert svc.audit_log.count(action="ingest") == 1
    Path(path).unlink(missing_ok=True)


def test_service_ask_audits_request():
    svc = _service()
    path = _csv_file()
    svc.ingest(path, "甲公司", 2025, user=_analyst())
    svc.ask("甲公司2025年资产总计是多少？", user=_analyst())
    assert svc.audit_log.count(action="ask") >= 1
    Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# AgentGraph 权限接入
# ---------------------------------------------------------------------------
def test_graph_blocks_out_of_scope_company():
    audit = AuditLog(":memory:")
    graph = AgentGraph(_store_with_facts(), audit_log=audit)
    out = graph.run("营收是多少", company_name="乙丙公司", user=_analyst())
    blocked = [r for r in out.get("tool_results", []) if r.error and r.error.type == "hook_blocked"]
    assert blocked
    assert audit.count(action="access_denied") == 1
