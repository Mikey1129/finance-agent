"""agent.skills.registry —— 第一批 5 个 Skill 的实现与静态注册（方案 5.7）。

每个 Skill 组合多个工具，产出结构化 SkillResult。注册表静态登记，不实现
动态发现/自动安装/任意代码执行（方案 5.7 硬要求）。
"""

from __future__ import annotations

from agent.skills.base import BaseSkill, SkillResult, SkillSection, _sources_of
from agent.tools import ToolStatus
from finance.metrics import METRIC_DEFINITIONS

# 指标 code 子集（各 Skill 依赖的指标）
_PROFIT_CODES = ["gross_margin", "net_margin", "roa", "roe"]
_CASHFLOW_CODES = ["operating_cash_flow", "ocf_to_net_profit", "profit_cash_flow_divergence"]
_RISK_CODES = [
    "debt_to_asset_ratio", "current_ratio", "quick_ratio",
    "accounts_receivable_change", "inventory_change", "profit_cash_flow_divergence",
]
_SUMMARY_CODES = ["gross_margin", "net_margin", "roe", "revenue_growth", "net_profit_growth"]


def _fmt_metric_rows(metrics: dict) -> list[dict]:
    """把 {code: MetricResult} 转成前端友好的行（ratio/growth 显示百分比）。"""
    rows: list[dict] = []
    for m in metrics.values():
        kind = METRIC_DEFINITIONS[m.code].kind if m.code in METRIC_DEFINITIONS else ""
        base = {
            "code": m.code, "name": m.name, "group": m.group,
            "formula": m.formula, "is_signal": m.is_signal,
        }
        if m.computed:
            if kind in ("ratio", "growth"):
                base.update(value=round(m.value, 6), display=f"{m.value:.2%}")
            else:
                base.update(value=round(m.value, 6), display=f"{m.value:,.2f}")
        else:
            base.update(value=None, display=m.status.value)
        rows.append(base)
    return rows


def _fact_rows(facts) -> list[dict]:
    return [
        {
            "item": f.standard_item, "value": f.value, "unit": f.unit,
            "period": f.period, "scope": f.consolidation_scope,
        }
        for f in facts
        if f.value is not None
    ]


def _evidence_rows(chunks) -> list[dict]:
    return [
        {
            "chunk_id": c.chunk_id, "text": c.text[:200],
            "page": c.page_start, "section": c.section_path,
        }
        for c in chunks
    ]


# ---------------------------------------------------------------------------
# 1. 三表综合分析
# ---------------------------------------------------------------------------
class FinancialStatementAnalysisSkill(BaseSkill):
    code = "financial_statement_analysis"
    name = "三表综合分析"
    description = "概览资产负债表、利润表、现金流量表核心科目并做平衡校验。"
    applicable = "需要快速了解一家公司财务全貌时"

    def run(self, company_name: str, fiscal_year: int | None = None) -> SkillResult:
        facts = self._facts(company_name, fiscal_year)
        key_items = {
            "total_assets", "total_liabilities", "total_equity",
            "revenue", "net_profit", "operating_cash_flow",
        }
        key_facts = [f for f in facts if f.standard_item in key_items]

        vr = self._invoke(
            "validate_result", company_name=company_name, fiscal_year=fiscal_year, check="balance"
        )
        report = vr.data
        balance_ok = bool(report) and report.errors == 0
        validation_rows = [
            {"level": i.level, "message": i.message}
            for i in (report.issues if report else [])
        ]

        if not facts:
            return SkillResult(
                skill=self.code, name=self.name, status="insufficient",
                summary=f"未找到 {company_name} 的财务数据",
            )

        summary = f"{company_name} 三表概览：资产 {self._fmt_sum(facts, 'total_assets')}，"
        summary += f"负债 {self._fmt_sum(facts, 'total_liabilities')}，权益 {self._fmt_sum(facts, 'total_equity')}，"
        summary += f"营收 {self._fmt_sum(facts, 'revenue')}，净利润 {self._fmt_sum(facts, 'net_profit')}。"
        summary += "资产负债表平衡 ✓" if balance_ok else "资产负债表平衡 ⚠ 未通过"

        warnings = [] if balance_ok else ["资产负债表不平衡，见校验分节"]
        return SkillResult(
            skill=self.code, name=self.name, summary=summary,
            sections=[
                SkillSection(heading="三表核心科目", kind="facts", rows=_fact_rows(key_facts)),
                SkillSection(heading="平衡校验", kind="validation", rows=validation_rows),
            ],
            sources=_sources_of([self._invoke("query_financial_fact", company_name=company_name, fiscal_year=fiscal_year), vr]),
            warnings=warnings,
        )

    @staticmethod
    def _fmt_sum(facts, item: str) -> str:
        vals = [f.value for f in facts if f.standard_item == item and f.value is not None]
        return f"{max(vals):,.2f} 元" if vals else "—"


# ---------------------------------------------------------------------------
# 2. 盈利能力分析
# ---------------------------------------------------------------------------
class ProfitabilityAnalysisSkill(BaseSkill):
    code = "profitability_analysis"
    name = "盈利能力分析"
    description = "计算毛利率、净利率、ROA、ROE 并判断盈利水平。"
    applicable = "评估一家公司赚钱能力与资本回报时"

    def run(self, company_name: str, fiscal_year: int | None = None) -> SkillResult:
        if fiscal_year is None:
            return SkillResult(
                skill=self.code, name=self.name, status="insufficient",
                summary="盈利能力分析需要指定年度",
            )
        metrics = self._metrics(company_name, fiscal_year, _PROFIT_CODES)
        if not metrics:
            return SkillResult(
                skill=self.code, name=self.name, status="insufficient",
                summary=f"无 {company_name} {fiscal_year} 的盈利数据",
            )

        roe = metrics.get("roe")
        warnings: list[str] = []
        if roe and roe.computed and roe.value is not None and roe.value < 0:
            warnings.append("ROE 为负，公司处于亏损状态")

        computed = [m for m in metrics.values() if m.computed]
        if computed:
            summary = "、".join(f"{m.name} {m.value:.2%}" for m in computed[:4])
        else:
            summary = "盈利指标均因数据不足无法计算"
        return SkillResult(
            skill=self.code, name=self.name, summary=f"{company_name} {fiscal_year}：{summary}",
            sections=[SkillSection(heading="盈利指标", kind="metrics", rows=_fmt_metric_rows(metrics))],
            sources=_sources_of([self._invoke("compute_metric", company_name=company_name, fiscal_year=fiscal_year, metric_codes=_PROFIT_CODES)]),
            warnings=warnings,
        )


# ---------------------------------------------------------------------------
# 3. 现金流质量分析
# ---------------------------------------------------------------------------
class CashFlowAnalysisSkill(BaseSkill):
    code = "cash_flow_analysis"
    name = "现金流质量分析"
    description = "分析经营现金流净额、现金覆盖倍数与利润现金流背离。"
    applicable = "判断盈利是否得到真金白银支撑时"

    def run(self, company_name: str, fiscal_year: int | None = None) -> SkillResult:
        if fiscal_year is None:
            return SkillResult(
                skill=self.code, name=self.name, status="insufficient",
                summary="现金流质量分析需要指定年度",
            )
        metrics = self._metrics(company_name, fiscal_year, _CASHFLOW_CODES)
        if not metrics:
            return SkillResult(
                skill=self.code, name=self.name, status="insufficient",
                summary=f"无 {company_name} {fiscal_year} 的现金流数据",
            )

        ocf = metrics.get("operating_cash_flow")
        ratio = metrics.get("ocf_to_net_profit")
        divergence = metrics.get("profit_cash_flow_divergence")
        warnings: list[str] = []
        if ocf and ocf.computed and ocf.value is not None and ocf.value < 0:
            warnings.append("经营现金流净额为负，主业不造血")
        if ratio and ratio.computed and ratio.value is not None and ratio.value < 0.5:
            warnings.append("经营现金流/净利润比 < 0.5，盈利现金支撑偏弱")
        if divergence and divergence.computed and divergence.value is not None and divergence.value > 0:
            warnings.append("利润与现金流背离额为负向信号（利润缺乏现金支撑，需调查）")

        evidence = self._evidence("经营活动现金流量净额变动", company_name, fiscal_year)

        summary_parts = []
        if ocf and ocf.computed:
            summary_parts.append(f"经营现金流净额 {ocf.value:,.2f} 元")
        if ratio and ratio.computed:
            summary_parts.append(f"现金覆盖倍数 {ratio.value:.2f}")
        summary = f"{company_name} {fiscal_year}：" + "，".join(summary_parts) + "。"

        return SkillResult(
            skill=self.code, name=self.name, summary=summary,
            sections=[
                SkillSection(heading="现金流指标", kind="metrics", rows=_fmt_metric_rows(metrics)),
                SkillSection(heading="现金流相关证据", kind="evidence", rows=_evidence_rows(evidence)),
            ],
            sources=_sources_of([
                self._invoke("compute_metric", company_name=company_name, fiscal_year=fiscal_year, metric_codes=_CASHFLOW_CODES),
                self._invoke("retrieve_evidence", query="经营活动现金流量净额变动", company_name=company_name, fiscal_year=fiscal_year),
            ]),
            warnings=warnings,
        )


# ---------------------------------------------------------------------------
# 4. 财务风险信号分析
# ---------------------------------------------------------------------------
class FinancialRiskAnalysisSkill(BaseSkill):
    code = "financial_risk_analysis"
    name = "财务风险信号分析"
    description = "扫描偿债能力与财务质量信号，提示值得进一步调查的异常。"
    applicable = "识别一家公司的财务风险时（信号非结论）"

    def run(self, company_name: str, fiscal_year: int | None = None) -> SkillResult:
        if fiscal_year is None:
            return SkillResult(
                skill=self.code, name=self.name, status="insufficient",
                summary="财务风险分析需要指定年度",
            )
        metrics = self._metrics(company_name, fiscal_year, _RISK_CODES)
        if not metrics:
            return SkillResult(
                skill=self.code, name=self.name, status="insufficient",
                summary=f"无 {company_name} {fiscal_year} 的财务数据",
            )

        warnings: list[str] = []
        debt = metrics.get("debt_to_asset_ratio")
        current = metrics.get("current_ratio")
        if debt and debt.computed and debt.value is not None and debt.value > 0.7:
            warnings.append("资产负债率 > 70%，长期偿债压力偏高")
        if current and current.computed and current.value is not None and current.value < 1.0:
            warnings.append("流动比率 < 1，短期偿债能力偏弱")

        signal_metrics = {m.code: m for m in metrics.values() if m.computed and m.is_signal}
        signal_rows = [
            {**row, "note": "信号非结论，需结合业务调查"}
            for row in _fmt_metric_rows(signal_metrics)
        ]
        solvency_rows = _fmt_metric_rows({
            k: v for k, v in metrics.items() if k in ("debt_to_asset_ratio", "current_ratio", "quick_ratio")
        })

        if warnings:
            summary = "发现 " + str(len(warnings)) + " 个风险信号；另有财务质量信号（应收/存货/背离）需关注"
        else:
            summary = "偿债指标未见明显异常；财务质量信号见下（信号非结论）"

        return SkillResult(
            skill=self.code, name=self.name, summary=f"{company_name} {fiscal_year}：{summary}",
            sections=[
                SkillSection(heading="偿债能力", kind="metrics", rows=solvency_rows),
                SkillSection(heading="财务质量信号", kind="signals", rows=signal_rows),
            ],
            sources=_sources_of([self._invoke("compute_metric", company_name=company_name, fiscal_year=fiscal_year, metric_codes=_RISK_CODES)]),
            warnings=warnings,
        )


# ---------------------------------------------------------------------------
# 5. 年报摘要及关键事项
# ---------------------------------------------------------------------------
class AnnualReportSummarySkill(BaseSkill):
    code = "annual_report_summary"
    name = "年报摘要及关键事项"
    description = "提炼关键财务数字、核心指标与经营讨论关键事项。"
    applicable = "快速了解一份年报的核心内容时"

    def run(self, company_name: str, fiscal_year: int | None = None) -> SkillResult:
        facts = self._facts(company_name, fiscal_year)
        key_items = {"revenue", "net_profit", "total_assets", "operating_cash_flow"}
        key_facts = [f for f in facts if f.standard_item in key_items]

        metrics = self._metrics(company_name, fiscal_year, _SUMMARY_CODES) if fiscal_year else {}

        evidence = self._evidence("经营情况 管理层讨论与分析", company_name, fiscal_year)

        if not facts and not metrics:
            return SkillResult(
                skill=self.code, name=self.name, status="insufficient",
                summary=f"未找到 {company_name} 的年报数据",
            )

        summary = f"{company_name} 年报摘要："
        summary += "、".join(f"{f.standard_item}={f.value:,.0f}" for f in key_facts[:4])
        summary += "。"

        return SkillResult(
            skill=self.code, name=self.name, summary=summary,
            sections=[
                SkillSection(heading="关键财务数字", kind="facts", rows=_fact_rows(key_facts)),
                SkillSection(heading="核心指标", kind="metrics", rows=_fmt_metric_rows(metrics)),
                SkillSection(heading="经营关键事项", kind="evidence", rows=_evidence_rows(evidence)),
            ],
            sources=_sources_of([
                self._invoke("query_financial_fact", company_name=company_name, fiscal_year=fiscal_year),
                self._invoke("retrieve_evidence", query="经营情况 管理层讨论与分析", company_name=company_name, fiscal_year=fiscal_year),
            ]),
        )


# ---------------------------------------------------------------------------
# 静态注册表
# ---------------------------------------------------------------------------
_SKILL_CLASSES = [
    FinancialStatementAnalysisSkill,
    ProfitabilityAnalysisSkill,
    CashFlowAnalysisSkill,
    FinancialRiskAnalysisSkill,
    AnnualReportSummarySkill,
]


def build_skills(store, chunks=None) -> dict[str, BaseSkill]:
    """实例化并静态注册全部 Skill。"""
    return {cls.code: cls(store, chunks) for cls in _SKILL_CLASSES}


def list_skill_meta() -> list[dict]:
    """Skill 元信息（供界面展示/选择）。"""
    return [
        {"code": cls.code, "name": cls.name, "description": cls.description, "applicable": cls.applicable}
        for cls in _SKILL_CLASSES
    ]
