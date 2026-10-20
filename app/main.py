"""app.main —— FastAPI 应用（阶段 4 产品界面）。

接口：上传财报 / 提问 / 已入库公司 / 运行记录 / 健康检查。
前端：app/static/index.html 极简单页（上传 + 对话 + 结果 + 引用跳转）。

启动：`uvicorn app.main:app --reload`（本地运行，方案 11「本地运行 → Docker」）。
测试：`create_app(FinanceService(":memory:", ":memory:"))` 注入进程内服务，隔离状态。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.service import FinanceService

_STATIC_DIR = Path(__file__).resolve().parent / "static"


class AskRequest(BaseModel):
    query: str
    thread_id: str | None = None


def create_app(service: FinanceService | None = None) -> FastAPI:
    """构建 FastAPI 应用。service 可注入（测试用 :memory:），缺省为持久化单例。"""
    svc = service or FinanceService()
    application = FastAPI(title="企业财报智能分析 Agent", version="0.1.0")

    @application.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "companies": svc.companies()}

    @application.post("/api/upload")
    def upload(
        file: UploadFile = File(...),
        company_name: str = Form(""),
        fiscal_year: int = Form(...),
        statement_type: str | None = Form(None),
    ) -> dict:
        """上传财报文件（PDF 自动三大报表；CSV/Excel 按 statement_type），解析入库。"""
        if not company_name.strip():
            raise HTTPException(status_code=400, detail="company_name 不能为空")
        suffix = Path(file.filename or "").suffix or ".csv"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name
        try:
            return svc.ingest(
                tmp_path,
                company_name.strip(),
                fiscal_year,
                statement_type=statement_type,
            )
        except Exception as exc:  # 解析失败回 400 而非 500，便于前端提示
            raise HTTPException(status_code=400, detail=f"解析失败: {exc}") from exc
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @application.post("/api/ask")
    def ask(req: AskRequest) -> dict:
        query = req.query.strip()
        if not query:
            raise HTTPException(status_code=400, detail="query 不能为空")
        if not svc.companies():
            raise HTTPException(status_code=400, detail="请先上传财报")
        return svc.ask(query, thread_id=req.thread_id)

    @application.get("/api/companies")
    def companies() -> dict:
        return {"companies": svc.companies()}

    @application.get("/api/runs")
    def runs(limit: int = 50) -> dict:
        return {"runs": svc.runs(limit=limit)}

    @application.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> dict:
        run = svc.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @application.get("/")
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    # 静态资源（CSS/JS）挂载在最后，避免拦截 API 路由
    application.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    return application


app = create_app()
