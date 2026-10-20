"""compare_companies 跨公司横向比较测试（阶段 5）。"""

from __future__ import annotations

from agent.hooks import HookManager, PermissionHook
from agent.tools import ToolStatus, build_tools
from app.service import FinanceService
from auth import Role, User
from domain import FinancialFact, StatementType
from finance.store import Store


def _two_company_store() -> Store:
    s = Store(":memory:")
    s.insert_financial_facts(
        [
            FinancialFact(
                fact_id="a1", report_id="ra", company_name="甲公司", fiscal_year=2024,
                raw_item="revenue", standard_item="revenue", value=1000.0,
                statement_type=StatementType.INCOME_STATEMENT, period="2024",
            ),
            FinancialFact(
                fact_id="a2", report_id="ra", company_name="甲公司", fiscal_year=2024,
                raw_item="net_profit", standard_item="net_profit", value=100.0,
                statement_type=StatementType.INCOME_STATEMENT, period="2024",
            ),
            FinancialFact(
                fact_id="b1", report_id="rb", company_name="乙丙公司", fiscal_year=2024,
                raw_item="revenue", standard_item="revenue", value=2000.0,
                statement_type=StatementType.INCOME_STATEMENT, period="2024",
            ),
            FinancialFact(
                fact_id="b2", report_id="rb", company_name="乙丙公司", fiscal_year=2024,
                raw_item="net_profit", standard_item="net_profit", value=100.0,
                statement_type=StatementType.INCOME_STATEMENT, period="2024",
            ),
        ]
    )
    return s


def _analyst() -> User:
    return User(user_id="u-analyst", role=Role.ANALYST, company_scope=["甲公司"])


def _service() -> FinanceService:
    svc = FinanceService(":memory:", ":memory:", ":memory:", ":memory:")
    svc.store = _two_company_store()
    return svc


# -- 工具层 ---------------------------------------------------------------
def test_compare_companies_tool():
    r = build_tools(_two_company_store()).invoke(
        "compare_companies",
        company_names=["甲公司", "乙丙公司"], fiscal_year=2024, metric_codes=["net_margin"],
    )
    assert r.status is ToolStatus.SUCCESS
    assert abs(r.data["甲公司"]["net_margin"].value - 0.1) < 1e-9
    assert abs(r.data["乙丙公司"]["net_margin"].value - 0.05) < 1e-9


def test_compare_companies_requires_two():
    r = build_tools(_two_company_store()).invoke(
        "compare_companies", company_names=["甲公司"], fiscal_year=2024
    )
    assert r.status is ToolStatus.ERROR
    assert r.error.type == "invalid_param"


def test_compare_companies_dedup_then_too_few():
    r = build_tools(_two_company_store()).invoke(
        "compare_companies", company_names=["甲公司", "甲公司"], fiscal_year=2024
    )
    assert r.status is ToolStatus.ERROR  # 去重后只剩 1 家


def test_compare_companies_unknown_metric():
    r = build_tools(_two_company_store()).invoke(
        "compare_companies", company_names=["甲公司", "乙丙公司"],
        fiscal_year=2024, metric_codes=["nope"],
    )
    assert r.status is ToolStatus.ERROR
    assert r.error.type == "unknown_metric"


def test_compare_companies_no_data_company():
    r = build_tools(_two_company_store()).invoke(
        "compare_companies", company_names=["甲公司", "无数据公司"], fiscal_year=2024
    )
    assert r.status is ToolStatus.SUCCESS
    assert r.data["甲公司"] is not None
    assert r.data["无数据公司"] is None
    assert any("无数据公司" in w for w in r.warnings)


# -- 权限层 ---------------------------------------------------------------
def test_permission_hook_blocks_company_list():
    hm = HookManager([PermissionHook()])
    r = hm.invoke(
        build_tools(_two_company_store()), "compare_companies",
        user=_analyst(), company_names=["甲公司", "乙丙公司"], fiscal_year=2024,
    )
    assert r.status is ToolStatus.ERROR
    assert r.error.type == "hook_blocked"


def test_permission_hook_allows_in_scope_list():
    hm = HookManager([PermissionHook()])
    r = hm.invoke(
        build_tools(_two_company_store()), "compare_companies",
        user=User(user_id="u", role=Role.ANALYST, company_scope=["甲公司", "乙丙公司"]),
        company_names=["甲公司", "乙丙公司"], fiscal_year=2024,
    )
    assert r.status is ToolStatus.SUCCESS


# -- 服务层 ---------------------------------------------------------------
def test_service_compare_companies():
    svc = _service()
    result = svc.compare_companies(["甲公司", "乙丙公司"], 2024, metric_codes=["net_margin"])
    assert abs(result["comparison"]["甲公司"]["net_margin"]["value"] - 0.1) < 1e-9
    assert abs(result["comparison"]["乙丙公司"]["net_margin"]["value"] - 0.05) < 1e-9
    assert svc.audit_log.count(action="compare") == 1


def test_service_compare_denied_by_scope():
    svc = _service()
    result = svc.compare_companies(["甲公司", "乙丙公司"], 2024, user=_analyst())
    assert result["error"] == "forbidden"
    assert "乙丙公司" in result["denied"]
    assert svc.audit_log.count(action="access_denied") == 1
