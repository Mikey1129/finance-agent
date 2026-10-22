# 企业财报智能分析 Agent（方案 11「本地运行 → Docker」，阶段 5）
# 多阶段构建：先 node 构建前端（Vue/Vite → dist），再 python 运行镜像拷贝 dist。
# 构建：docker build -t finance-agent .
# 运行：docker run -p 8000:8000 -v "$(pwd)/storage:/app/storage" finance-agent

# ---- 前端构建阶段 ----
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 运行时阶段 ----
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 系统运行库：numpy/pypdfium2 依赖 OpenMP（libgomp1）
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 依赖：排除可选重依赖 sentence-transformers（torch，BGE 默认关闭），减小镜像体积
COPY requirements.txt .
RUN grep -v -E "^sentence-transformers" requirements.txt > /tmp/requirements-core.txt \
    && pip install -r /tmp/requirements-core.txt

# 代码 + 前端构建产物
COPY . .
COPY --from=frontend /fe/dist ./frontend/dist

# 运行时数据目录（DuckDB / SQLite / 上传文件），由 volume 挂载
RUN mkdir -p storage && chmod 777 storage

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
