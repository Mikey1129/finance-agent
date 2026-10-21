# 企业财报智能分析 Agent

FinRAG（财报数值推理 RAG）的升级版 —— 从「RAG 玩具」到「企业级可落地的财务分析 Agent」。

**四句目标**：数据可信、计算可靠、分析有据、过程可追溯。
**核心原则**：财务事实由数据层提供，财务数字由计算层产生，分析结论由证据与模型共同形成，可信性由校验和评测验证。

> 完整方案见 [`方案.md`](./方案.md)（定稿 v2，15 章 + 未来扩展方向）。

## 开发进度

| 阶段 | 状态 | 交付物 |
|---|---|---|
| 1 数据基础 | ✅ 基本完成 | ReportDoc/FinancialFact/Provenance + Excel/CSV + 原生 PDF + 结构切块 + DuckDB + 基础校验 |
| 2 分析能力 | ✅ 基本完成 | 科目映射 + 指标注册中心（16 指标）+ 多期比较 + 工具注册/Schema |
| 3 Agent 闭环 | ✅ 完成 | LangGraph 编排 + Intent Router + AgentState + Checkpointer + Function Calling + BM25 召回 + 证据组装 |
| 4 产品界面 | ✅ 基本完成 | 上传/对话/指标卡片/趋势/技能/导出/多轮续接 + Skill + 长期记忆 + 评测 D |
| 5 企业增强 | ✅ 基本完成 | 权限/审计 + 多公司比较 + 缓存/上下文 + 复杂 PDF 视觉 + Docker |

## 目录结构

```
finance-agent/
├── app/           # FastAPI 接口 + Web 界面（service/main/static）
├── domain/        # ReportDoc、FinancialFact、Provenance 数据模型
├── ingestion/     # Excel/CSV、PDF 解析
├── finance/       # DuckDB 存储、Taxonomy 科目映射、Metrics、Validation
├── retrieval/     # 结构切块、BM25/向量检索
├── agent/         # state、graph、router、planner、tools、context、memory、hooks
├── reporting/     # 分析报告
├── evaluation/    # 数据/计算/检索/端到端评测
├── tests/         # pytest 单测
├── storage/       # 原始文件与本地数据（git 忽略）
└── config/        # 配置与规则版本
```

## 阶段 1 成果（数据基础）

- **数据模型**：`domain/` 三模型（ReportDoc / FinancialFact / Provenance）+ EvidenceChunk，落实方案第 6 章 7 条财务数据规则（单位与数值分开、期间类型、合并范围、原始+标准化+派生、数据状态枚举、版本追溯）。
- **科目映射**：`finance/taxonomy.py` CAS 中文科目（留 IFRS/US GAAP 接口），内置元/千元/万元/亿元单位标尺。
- **存储**：`finance/store.py` DuckDB 四表 + dict 风格查询；`finance/validation.py` 基础校验 + 资产负债表平衡 sanity check。
- **解析**：`ingestion/excel_parser.py`（Excel/CSV）+ `ingestion/pdf_parser.py`（原生 PDF）。
- **切块**：`retrieval/chunker.py` 结构感知切块（章节/段落/父子关系/页码）。

**评测 A（字段级数据提取）**：对 FinQA dev.json 前 200 样本的 2956 个财报数值单元格跑 `parse_number`，解析成功率 **98.07%**（剩余 1.29% 为非数字标签文本）。覆盖 `$` 符号、括号负数、千分位、并列格式「A ( B )」、单位标注等中文财报数字格式。

## 阶段 2 成果（分析能力）

- **指标注册中心**：`finance/metrics.py` 第一版 6 组 16 指标（成长/盈利/偿债/现金流/运营效率/财务质量），`MetricDefinition` 存公式与口径，`MetricResult` 保留分子/分母/输入科目以便追溯；分母为 0 或缺科目时返回 `insufficient_data` / `divide_by_zero`（不编造假数）。
- **工具注册与 Schema**：`agent/tools.py` 5 个核心工具（`query_financial_fact` / `compute_metric` / `compare_periods` / `retrieve_evidence` / `validate_result`），输入用 Pydantic Schema、统一返回 `ToolResult`（状态/数据/单位/口径/来源/错误），`ToolRegistry` 导出 Function Calling 风格 schema。
- **科目映射与多期比较**：`finance/taxonomy.py` 归一化/前缀剥离/紧凑匹配；`compute_series` 跨期序列。

**评测 B（指标计算）**：`evaluation/eval_metrics.py` 用真实财报（公牛集团 2024 年报）端到端计算全部指标，断言关键盈利/偿债指标落在合理区间，防止科目映射污染导致指标静默跑偏；公式与边界（分母为 0 / 缺科目 / 无上期 / scope 过滤）由 `tests/test_metrics.py` + `tests/test_tools.py` 覆盖。

## 阶段 3 成果（Agent 闭环 · 确定性骨架）

- **AgentState + Intent Router**：`agent/state.py` 统一任务状态（会话标识/业务上下文/任务状态/执行产物/控制字段）；`agent/router.py` 规则版分流 A/B/C/CLARIFY + 实体提取（公司/期间/科目/指标，含口语简称）。
- **BM25 混合召回**：`retrieval/retriever.py` 纯 Python Okapi BM25 + 元数据过滤（公司/年度/章节）+ 去重；`retrieve_evidence` 工具已接入。
- **上下文管理**：`agent/context.py` ContextResolver 多轮消歧（新指令覆盖、近 N 年）+ ContextBuilder 按优先级组装裁剪到可配置 Token 预算。
- **证据组装**：`agent/evidence.py` EvidenceAssembler + EvidencePack（来源引用/冲突披露/充分性检查）。
- **可信性 Hook**：`agent/hooks.py` 三层校验（入口参数范围/预算/来源完整性/提示注入）。
- **记忆与规划**：`agent/memory.py` 长期记忆（读写/失效/删除）+ 会话历史；`agent/planner.py` 三条路径任务规划。
- **LangGraph 编排**：`agent/graph.py` 状态图 resolve→plan→execute→validate→answer，CLARIFY 短路；确定性骨架（不接 LLM）。

**待阶段 4**：Checkpointer（状态持久化/恢复）、Function Calling（LLM 决策层接入）、AnalysisRun 落库、BGE/Reranker。

## 阶段 4 成果（检索评测 / 语义召回 / 产品界面 / Skill / 长期记忆 / 评测 D）

**评测 C（RAG 检索）**：`evaluation/eval_retrieval.py` 对真实财报（公牛集团 2024 年报，606 片段）的 10 个财务查询计算 Recall@5 / Precision@5 / MRR / nDCG / Latency，BM25 为基线：**Recall 0.632 / Precision 0.300 / MRR 0.667 / nDCG 0.534，Latency 13.7ms**。

**BGE 语义召回（实测劣化，默认关闭）**：`retrieval/embedding.py` 封装 bge-small-zh-v1.5，`retriever.py` 用 RRF 与 BM25 混合。对比评测显示混合召回**全面劣于纯 BM25**——Recall 0.632→0.325、MRR 0.667→0.400、Latency 13.7ms→5.2s：财报查询依赖精确科目词与数字，通用语义模型对术语/数字不敏感，反把「语义沾边但非当前科目」的片段挤进 top-k。故 `embedder` 可选、默认关闭（纯 BM25）；代码与单测保留作实验开关。

> 模型下载（可选）：`embedder=None` 时无需装 `sentence-transformers`。启用 BGE 前，先下载模型到 `models/bge-small-zh-v1.5`（HF 镜像或 modelscope），代码优先加载本地路径。

**Reranker 候选重排（DashScope qwen3-rerank，默认开启）**：`retrieval/reranker.py` 提供 `Reranker` 抽象 + `DashScopeReranker`（httpx 直连 `qwen3-rerank`，key 从 `DASHSCOPE_API_KEY` 读，缺 key / 请求失败 / 空结果降级为召回原顺序）+ `build_reranker()`（有 key 即自动启用）。`Retriever` 先召回候选池（`top_k×4`）再交给 reranker 对 `(query, doc)` 精细重排，纠正「语义沾边但非本科目」挤进 top-k 的问题。

**评测结论（公牛 2024 年报，606 chunk，k=5，真实 qwen3-rerank）**：BM25 基线 Recall@5=0.632 / nDCG@5=0.534 / MRR=0.667；**BM25+qwen3-rerank Recall@5=0.740 / nDCG@5=0.663 / MRR=0.703**——3 查询改善（营收 0.25→0.50、归母净利润 0.67→1.00、货币资金 0.50→1.00）、0 劣化，正中 BM25 精确数值查询的弱项。故**默认开启**：`build_reranker()` 检测到 `DASHSCOPE_API_KEY` 即启用，无 key 自动降级纯 BM25（行为与接入前一致）。代价：每查询延迟 8ms→~526ms（一次 API 调用）。`rank_fn` 可注入，单测不触网。

**评测 C 架构纠偏（数值查询走结构化 facts，Recall 0.740→1.000）**：上述 0.740 是「全文 chunk 检索」测**数值型查询**（存货/营收/现金流「是多少」）的结果——但这类问句不含答案数字，BM25 词法只能靠高频科目词匹配，命中率有天花板（实测扩候选池 / 表格行 chunk 化 / 去数字改写均不升反降）。数值查询的答案本在结构化 `financial_facts` 里（方案第 6 章「财务事实由数据层提供」）。故新增 `retrieval/fact_retriever.py`：`extract_item` 把问句映射到标准科目（长全称优先 + 口语简称如「归母净利润」），`FactRetriever` 按公司/年度/合并范围精确匹配 facts；评测集 `RetrievalQuery` 增加 `kind`/`gold_item`/`gold_values` 区分「数值型（fact）vs 定性型（chunk）」，`eval_retrieval_mixed` 分路径评测。**综合 Recall@5=1.000**（6 个数值查询走 facts 全命中，4 个定性查询走 chunk 保持 1.0），是「agent 用结构化数据回答数值问题」的真实能力，非文本检索的硬凑。

**定性检索评测扩容（51 个真实问题）**：上述定性查询（kind="chunk"）原只有 4 个，1.0 的 Recall 无统计意义。现从公牛 2024 年报 MD&A/附注 + 长鑫招股书审计报告/附注原文标注 **51 个定性问题**（公牛 32 + 长鑫 19，`evaluation/qualitative_queries.py`），每个标 gold_phrases（原文独特短语，去空白子串匹配），覆盖归因/业务/研发/风险/关联方/股份支付/金融风险/承诺/分部等类型。纯 BM25 基线（k=5）：**综合 Recall@5=0.892 / Precision@5=0.243 / MRR=0.785**（公牛 0.969/0.263/0.806，长鑫 0.763/0.211/0.750），43/51 查询 Recall=1.0。剩余失败（审计机构名、持股平台、越南子公司等低频实体/表格信息）暴露 BM25 词法检索的真实短板，正是后续 reranker/语义召回应重点补的方向。入口：`python -m evaluation.run_eval_retrieval`（逐条打印命中 + 汇总，`--reranker` 接入 qwen3-rerank，`--company` 选公司）；锚点命中 100% 由 `tests/test_qualitative_queries.py` 锁住防回归。

**产品界面**：`app/service.py`（FinanceService 服务层）+ `app/main.py`（FastAPI）+ `app/static/index.html`（深色金融风单页）。完整闭环——上传财报 → 对话提问（答案带来源引用 + 缺失/冲突提示）→ 指标概览卡片与趋势 → 技能分析 → 报告导出（Markdown）→ 多轮对话自动续接上次公司。

**Skill 技能层**：`agent/skills/` 5 个可复用技能（三表综合分析/盈利能力/现金流质量/财务风险信号/年报摘要），每个含适用范围/输入/流程/依赖工具/输出/校验规则/评测样例；复用 Hook 三层校验，静态注册不动态执行代码。

**长期记忆**：`agent/memory.py` `SqliteMemoryStore` 持久化（跨会话），问答把「最近分析公司」作兜底续接；界面暴露记忆增删查 API。

**评测 D（系统安全）**：`evaluation/eval_security.py` 六类场景（恶意文档/提示词注入/参数越界/预算循环终止/错误路由/记忆污染）端到端验证安全机制；顺带修复 graph/skills 的 Hook 列表漏接 `PromptInjectionHook` 的缺口。

## 阶段 5 成果（企业增强）

**权限控制**：`auth/models.py` 引入 `User` / `Role`（ADMIN / ANALYST / VIEWER）+ `AccessPolicy` 公司访问范围隔离（`company_scope`：None=全部、列表=指定公司、`[]`=无）。读/写分离：VIEWER 只读、ANALYST 读写范围内公司、ADMIN 全权限。**权限是可选增强**——不传 `user` 时行为与接入前完全一致（向后兼容既有骨架与测试）。

**审计日志**：`auth/audit.py` `AuditLog` SQLite 持久化，记录「谁在何时对什么资源做了什么、结果如何」（who/when/what/result）。与 `AnalysisRun`（怎么算出来的执行记录）分工明确。接入点：上传（`ingest`）、提问（`ask`）、越权拒绝（`access_denied`）均落审计；`FastAPI` 暴露 `/api/audit` 查询接口。

**工具层权限校验**：`agent/hooks.py` 新增 `PermissionHook`（入口层），`Hook.before/after` 统一透传 `user`；`agent/graph.py` 注入 `access_policy` / `audit_log`，`execute` 时把当前用户传入 Hook。越权工具调用被阻止（`hook_blocked`）并记审计。

**运行记录追溯用户**：`AnalysisRun` 增加 `user_id`，`finance/store.py` `analysis_runs` 表同步加列，每条分析可回溯到具体用户。

**多公司横向比较**：`agent/tools.py` 新增 `compare_companies` 工具（第 6 个核心工具），对多公司同一年度计算同一组指标；`PermissionHook` 已支持 `company_names` 列表的逐公司读权限校验（越权即阻止）；`app/service.py` 暴露 `compare_companies`（含权限拒绝 + 审计），`FastAPI` 新增 `/api/compare` 接口。

**缓存与上下文压缩**：`retrieval/cache.py` `RetrievalCache`（TTL + 容量上限，满时淘汰最早到期，命中返回浅拷贝），接入 `Retriever`（可选，默认关闭）；`agent/summary.py` `HistorySummarizer` 把多轮历史压缩为「最近 N 轮原始对话 + 更早历史的实体摘要」（提取公司/期间/科目/指标，不把模型结论当事实）。

**复杂 PDF 视觉解析兜底（Qwen3-VL）**：`ingestion/vision_parser.py` 提供 `is_scanned_pdf`（无文本层扫描件检测）+ `VisionParser` 抽象接口 + `QwenVLVisionParser`（DashScope OpenAI 兼容多模态，默认 `qwen3-vl-plus`，`vl_high_resolution_images` 提像素上限专治财报小字表格 OCR）+ `parse_pdf_with_fallback`（pdfplumber 优先，扫描件走视觉）。视觉识别输出「标题 + 单位 + markdown 表格」，复用 `_match_title` 定位报表范围、`parse_tabular` 提取 facts——扫描件同样能查数值、算指标、做资产负债表平衡校验。`build_vision_parser()` 从 `DASHSCOPE_VL_API_KEY`（回退 `DASHSCOPE_API_KEY`）读 key 自动启用，无 key 则 `FinanceService` 行为不变（仅 pdfplumber）。

**Docker 容器化**：`Dockerfile`（python:3.11-slim，排除可选重依赖 sentence-transformers/torch 减小镜像）+ `.dockerignore`（排除 storage/模型/缓存）+ `docker-compose.yml`（端口 8000 + storage volume 挂载 + 可选 API key 环境变量）。

## 快速开始

```bash
pip install -r requirements.txt
pytest            # 运行测试（217 个用例）
uvicorn app.main:app --reload   # 启动界面 http://127.0.0.1:8000
```
