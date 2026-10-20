# 企业财报智能分析 Agent

FinRAG（财报数值推理 RAG）的升级版 —— 从「RAG 玩具」到「企业级可落地的财务分析 Agent」。

**四句目标**：数据可信、计算可靠、分析有据、过程可追溯。
**核心原则**：财务事实由数据层提供，财务数字由计算层产生，分析结论由证据与模型共同形成，可信性由校验和评测验证。

> 完整方案见 [`方案.md`](./方案.md)（定稿 v2，15 章 + 未来扩展方向）。

## 开发进度

| 阶段 | 状态 | 交付物 |
|---|---|---|
| 1 数据基础 | ✅ 基本完成 | ReportDoc/FinancialFact/Provenance + Excel/CSV + 原生 PDF + 结构切块 + DuckDB + 基础校验 |
| 2 分析能力 | 未开始 | 科目映射 + 指标注册中心 + 多期比较 + 工具注册/Schema |
| 3 Agent 闭环 | 未开始 | LangGraph + Intent Router + AgentState + Function Calling + 混合召回 |
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

## 快速开始

```bash
pip install -r requirements.txt
pytest            # 运行测试（22 个用例）
```
