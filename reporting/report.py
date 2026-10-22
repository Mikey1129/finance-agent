"""reporting.report —— 带来源的分析报告渲染（Markdown）。

收拢 app/service.py 里散落的运行记录导出逻辑，作为「带来源的分析报告」的独立模块
（方案 12 章 reporting/report.py，P1）。归因报告的渲染仍留在 agent/attribution.py
（与归因数据结构 AttributionDraft 强耦合，属归因模块内聚，不强行拆出）。
"""

from __future__ import annotations

from typing import Any


def render_analysis_report(run: dict[str, Any]) -> str:
    """把一条 AnalysisRun 运行记录渲染成 Markdown 报告（可回放审计的产物）。

    run 来自 Store.get_analysis_run(run_id)（dict，字段见 domain.AnalysisRun）。
    """
    lines = [
        "# 财务分析报告", "",
        f"- 问题：{run.get('user_query')}",
        f"- 公司：{run.get('company_name') or '—'}",
        f"- 期间：{', '.join(run.get('periods', [])) or '—'}",
        f"- 意图：{run.get('intent') or '—'}",
        "", "## 结论", "",
        run.get("final_answer") or "（无）", "",
        "## 工具调用", "",
    ]
    for tc in run.get("tool_calls", []):
        src = ", ".join(tc.get("source_refs", []))
        line = f"- `{tc.get('tool')}`（{tc.get('status')}）：{tc.get('summary')}"
        lines.append(line + (f" [{src}]" if src else ""))
    if run.get("validation_summary"):
        lines += ["", "## 校验", ""]
        lines += [f"- {v}" for v in run["validation_summary"]]
    return "\n".join(lines)
