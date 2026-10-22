"""app.main —— FastAPI 应用（阶段 4 产品界面）。

接口：上传财报 / 提问 / 已入库公司 / 运行记录 / 健康检查。
前端：Vue 3 + Vite 单页应用（frontend/ 构建产物 dist/，由 / 与 SPA fallback 服务）。

启动：`uvicorn app.main:app --reload`（本地运行，方案 11「本地运行 → Docker」）。
测试：`create_app(FinanceService(":memory:", ":memory:"))` 注入进程内服务，隔离状态。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.service import FinanceService
from dotenv import load_dotenv

# 加载项目根目录 .env，本地 `uvicorn app.main:app` 直接跑时也能读到 DEEPSEEK_API_KEY 等
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# 前端构建产物目录（Vite `npm run build` 输出 dist/）；未构建时 API 照常可用，仅静态页缺失
_DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"


class AskRequest(BaseModel):
    query: str
    thread_id: str | None = None


class MemorySetRequest(BaseModel):
    key: str
    value: Any
    kind: str = "reusable"
    ttl_seconds: int | None = None


class SkillRunRequest(BaseModel):
    company_name: str
    fiscal_year: int | None = None


class CompareRequest(BaseModel):
    company_names: list[str]
    fiscal_year: int
    metric_codes: list[str] | None = None
    scope: str = "consolidated"


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
        except PermissionError as exc:  # 阶段 5：越权上传回 403
            raise HTTPException(status_code=403, detail=str(exc)) from exc
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

    @application.get("/api/audit")
    def audit(user_id: str | None = None, action: str | None = None, limit: int = 100) -> dict:
        """审计日志（阶段 5：谁在何时对什么资源做了什么）。"""
        return {"events": svc.audit_events(user_id=user_id, action=action, limit=limit)}

    @application.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> dict:
        run = svc.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @application.get("/api/memory")
    def memory_list() -> dict:
        return {"items": svc.memory_items()}

    @application.post("/api/memory")
    def memory_set(req: MemorySetRequest) -> dict:
        return svc.set_memory(req.key, req.value, kind=req.kind, ttl_seconds=req.ttl_seconds)

    @application.delete("/api/memory/{key}")
    def memory_delete(key: str) -> dict:
        if not svc.delete_memory(key):
            raise HTTPException(status_code=404, detail="memory key not found")
        return {"deleted": key}

    @application.get("/api/company/{company_name}/overview")
    def overview(company_name: str, year: int) -> dict:
        return svc.overview(company_name, year)

    @application.post("/api/compare")
    def compare(req: CompareRequest) -> dict:
        """跨公司横向比较（阶段 5：多公司同一年度同一组指标）。"""
        if not req.company_names:
            raise HTTPException(status_code=400, detail="company_names 不能为空")
        result = svc.compare_companies(
            req.company_names, req.fiscal_year,
            metric_codes=req.metric_codes, scope=req.scope,
        )
        if result.get("error") == "forbidden":
            raise HTTPException(status_code=403, detail=f"无权对比公司: {result.get('denied')}")
        if result.get("error"):
            raise HTTPException(status_code=400, detail=result.get("detail"))
        return result

    @application.get("/api/runs/{run_id}/export")
    def export_run(run_id: str) -> Response:
        md = svc.export_run(run_id)
        if md is None:
            raise HTTPException(status_code=404, detail="run not found")
        return Response(md, media_type="text/markdown")

    @application.get("/api/skills")
    def skills() -> dict:
        return {"skills": svc.list_skills()}

    @application.post("/api/skills/{code}/run")
    def run_skill(code: str, req: SkillRunRequest) -> dict:
        result = svc.run_skill(code, req.company_name, req.fiscal_year)
        if result is None:
            raise HTTPException(status_code=404, detail="skill not found")
        return result

    @application.get("/")
    def index() -> Response:
        idx = _DIST_DIR / "index.html"
        if not idx.is_file():
            return Response(
                "<h1>前端未构建</h1><p>请先在 frontend/ 目录运行 npm install && npm run build</p>",
                media_type="text/html",
            )
        return FileResponse(idx)

    # 静态资源（Vite 构建产物，base='/static/'）挂载在 /static，避免拦截 API 路由
    if _DIST_DIR.is_dir():
        application.mount("/static", StaticFiles(directory=str(_DIST_DIR)), name="static")

    # SPA fallback（Vue Router history 模式）：刷新/直达子路由回退到 index.html。
    # 必须最后注册，且跳过 /api 与 /static，否则会把 API 404 吞成 200 的 HTML。
    @application.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> Response:
        if full_path.startswith("api/") or full_path.startswith("static/"):
            raise HTTPException(status_code=404, detail="not found")
        idx = _DIST_DIR / "index.html"
        if not idx.is_file():
            raise HTTPException(status_code=404, detail="前端未构建")
        return FileResponse(idx)

    return application


app = create_app()
