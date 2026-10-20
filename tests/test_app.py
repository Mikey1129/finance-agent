"""app 界面集成测试：上传财报 → 问答 → 运行记录（FastAPI TestClient）。

用进程内服务（:memory:）隔离状态，不落 storage/，不触网。
"""

from fastapi.testclient import TestClient

from app.main import create_app
from app.service import FinanceService

_BALANCE_SHEET_CSV = (
    "项目,2025年12月31日,2024年12月31日\n"
    "货币资金,5000,4000\n"
    "应收账款,3000,2500\n"
    "资产总计,10000,8000\n"
    "负债合计,6000,5000\n"
    "所有者权益合计,4000,3000\n"
)


def _client() -> TestClient:
    return TestClient(create_app(FinanceService(":memory:", ":memory:", ":memory:")))


def _upload(client: TestClient, company: str = "测试公司", year: int = 2025):
    return client.post(
        "/api/upload",
        files={"file": ("balance.csv", _BALANCE_SHEET_CSV.encode("utf-8"), "text/csv")},
        data={"company_name": company, "fiscal_year": str(year)},
    )


def test_upload_csv_and_list_companies():
    client = _client()
    resp = _upload(client)
    assert resp.status_code == 200
    assert resp.json()["facts"] == 10  # 5 行 × 2 期
    assert "测试公司" in client.get("/api/companies").json()["companies"]


def test_ask_returns_fact_value():
    client = _client()
    _upload(client)
    resp = client.post("/api/ask", json={"query": "测试公司2025年资产总计是多少？"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert "total_assets=10000.0" in body["answer"]


def test_ask_records_run():
    client = _client()
    _upload(client)
    client.post("/api/ask", json={"query": "测试公司2025年资产总计是多少？"})
    runs = client.get("/api/runs").json()["runs"]
    assert len(runs) == 1
    assert runs[0]["user_query"] == "测试公司2025年资产总计是多少？"


def test_ask_requires_upload():
    client = _client()
    resp = client.post("/api/ask", json={"query": "测试公司资产总计"})
    assert resp.status_code == 400


def test_upload_missing_company():
    client = _client()
    resp = client.post(
        "/api/upload",
        files={"file": ("b.csv", _BALANCE_SHEET_CSV.encode("utf-8"), "text/csv")},
        data={"company_name": "", "fiscal_year": "2025"},
    )
    assert resp.status_code == 400


def test_memory_api():
    client = _client()
    resp = client.post(
        "/api/memory", json={"key": "unit_pref", "value": "万元", "kind": "preference"}
    )
    assert resp.status_code == 200
    items = client.get("/api/memory").json()["items"]
    assert any(i["key"] == "unit_pref" for i in items)
    assert client.delete("/api/memory/unit_pref").json()["deleted"] == "unit_pref"
    assert client.delete("/api/memory/unit_pref").status_code == 404


def test_ask_remembers_last_company():
    client = _client()
    _upload(client)
    # 第一次带公司名提问，记忆写入 last_company
    client.post("/api/ask", json={"query": "测试公司2025年资产总计是多少？"})
    # 第二次不提公司名，应续接上次公司
    resp = client.post("/api/ask", json={"query": "2025年资产总计是多少？"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert "total_assets=10000.0" in body["answer"]
