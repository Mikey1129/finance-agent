"""agent.reviewer 结论审查测试：批判性审查 + 降级 + 引用过滤 + key gating。"""

import json

from agent.attribution import (
    AttributionConclusion,
    AttributionDraft,
    AttributionReason,
    EvidenceLevel,
)
from agent.evidence import EvidencePack
from agent.reviewer import ConclusionReviewer, build_reviewer
from agent.tools import SourceRef
from domain import EvidenceChunk, FinancialFact, StatementType


# -- 测试数据 ---------------------------------------------------------------
def _pack():
    facts = [
        FinancialFact(
            fact_id="ocf-2024", report_id="r1", company_name="测试公司",
            fiscal_year=2024, raw_item="operating_cash_flow", standard_item="operating_cash_flow",
            value=180.0, unit="元", statement_type=StatementType.CASH_FLOW,
            period="2024", consolidation_scope="consolidated",
        ),
    ]
    chunks = [
        EvidenceChunk(
            chunk_id="c1", report_id="r1", company_name="测试公司", fiscal_year=2024,
            section_path="现金流量表附注", page_start=45, text="预收货款下降所致",
        ),
    ]
    return EvidencePack(
        query="为什么经营现金流下降",
        company_name="测试公司",
        periods=["2024"],
        financial_facts=facts,
        computed_metrics={},
        text_evidence=chunks,
    )


def _draft():
    conclusion = AttributionConclusion(
        summary="经营现金流下降主要由预收货款下降所致",
        reasons=[
            AttributionReason(
                claim="预收货款下降导致经营现金流下降",
                evidence_level=EvidenceLevel.DISCLOSED, refs=["C1"], basis="附注明写",
            ),
            AttributionReason(
                claim="经营现金流净额下降",
                evidence_level=EvidenceLevel.INFERRED, refs=["F1"], basis="数据方向",
            ),
        ],
        caveats=["推断类结论未获报告直接确认"],
    )
    mapping = {
        "F1": SourceRef(kind="fact", ref_id="ocf-2024", report_id="r1"),
        "C1": SourceRef(kind="chunk", ref_id="c1", report_id="r1", page=45),
    }
    return AttributionDraft(conclusion=conclusion, mapping=mapping)


_REVISED = json.dumps(
    {
        "summary": "经营现金流下降与预收货款下降相关",
        "reasons": [
            {"claim": "预收货款下降与现金流下降相关", "evidence_level": "inferred", "refs": ["C1"], "basis": "数据方向"},
        ],
        "caveats": ["报告未明确归因"],
    }
)


# -- ConclusionReviewer.review ---------------------------------------------
def test_reviewer_returns_revised_draft():
    # mock LLM 返回「因果改写为相关 + 降级 + 删除不支撑 reason」的修正版，应被透传
    reviewer = ConclusionReviewer(invoke=lambda s, u: _REVISED)
    draft = reviewer.review(_draft(), _pack())
    assert draft is not None
    assert draft.conclusion.summary == "经营现金流下降与预收货款下降相关"
    assert len(draft.conclusion.reasons) == 1  # 删掉了不支撑的那条
    assert draft.conclusion.reasons[0].evidence_level is EvidenceLevel.INFERRED  # 已降级
    assert draft.conclusion.reasons[0].refs == ["C1"]


def test_reviewer_prompt_includes_draft_and_evidence():
    captured: dict[str, str] = {}

    def fake(system: str, user: str) -> str:
        captured["system"] = system
        captured["user"] = user
        return _REVISED

    reviewer = ConclusionReviewer(invoke=fake)
    reviewer.review(_draft(), _pack())
    assert "审查" in captured["system"]  # 批判视角提示词
    assert "相关性" in captured["system"]
    assert "预收货款下降导致经营现金流下降" in captured["user"]  # 初稿 claim
    assert "[C1]" in captured["user"]  # 证据编号


def test_reviewer_non_json_returns_none():
    reviewer = ConclusionReviewer(invoke=lambda s, u: "not a json")
    assert reviewer.review(_draft(), _pack()) is None


def test_reviewer_empty_returns_none():
    reviewer = ConclusionReviewer(invoke=lambda s, u: "")
    assert reviewer.review(_draft(), _pack()) is None


def test_reviewer_invalid_level_returns_none():
    bad = json.dumps({"reasons": [{"claim": "x", "evidence_level": "bogus"}]})
    reviewer = ConclusionReviewer(invoke=lambda s, u: bad)
    assert reviewer.review(_draft(), _pack()) is None


def test_reviewer_invoke_raises_returns_none():
    def boom(system: str, user: str) -> str:
        raise RuntimeError("boom")

    reviewer = ConclusionReviewer(invoke=boom)
    assert reviewer.review(_draft(), _pack()) is None


def test_reviewer_filters_out_of_range_refs():
    # 修正版引用不存在的 F9，应被过滤（复用 _validate_conclusion 的 refs 校验）
    data = json.dumps(
        {"reasons": [{"claim": "x", "evidence_level": "disclosed", "refs": ["C1", "F9"]}]}
    )
    reviewer = ConclusionReviewer(invoke=lambda s, u: data)
    draft = reviewer.review(_draft(), _pack())
    assert draft is not None
    assert draft.conclusion.reasons[0].refs == ["C1"]


def test_reviewer_no_key_returns_none(monkeypatch):
    # 显式隔离环境变量：本地 .env 存在 DEEPSEEK_API_KEY 时（load_dotenv 会加载），
    # ConclusionReviewer(api_key=None) 会回退到 os.getenv 拿到真实 key 触发联网。
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    reviewer = ConclusionReviewer(api_key=None)
    assert reviewer.review(_draft(), _pack()) is None


# -- build_reviewer ---------------------------------------------------------
def test_build_reviewer_key_gating(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert build_reviewer() is None
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert build_reviewer() is not None
