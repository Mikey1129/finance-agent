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
| 3 Agent 闭环 | 🔄 大部分完成 | LangGraph 编排 + Intent Router + AgentState + BM25 混合召回 + 证据组装（Checkpointer/LLM 决策层待阶段 4） |
| 4 产品界面 | 未开始 | 上传/指标卡片/对话/引用/导出 + Skill + 长期记忆 |
| 5 企业增强 | 未开始 | 复杂 PDF 视觉 + 多公司比较 + 审计/权限 + Docker |

## 目录结构

```
finance-agent/
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

## 快速开始

```bash
pip install -r requirements.txt
pytest            # 运行测试（109 个用例）
```
