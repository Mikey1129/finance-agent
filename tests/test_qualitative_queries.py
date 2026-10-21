"""定性检索评测集的完整性 + 锚点命中验证（防回归）。

- 规模门槛：定性查询总量 ≥30、公牛 ≥20、长鑫 ≥15（否则 1.0 无统计意义）。
- 结构合法：qid 唯一、问题与锚点非空、公司/期间字段合法。
- 锚点命中：每个 gold 锚点必须命中对应公司 chunk 的至少一段文本（命中率须 100%，
  锚点悬空 = 标注错误，评测失真）。依赖 storage 下已切块的 json（git 忽略），
  缺失时跳过（与 test_metrics 的真实 PDF 锚定测试同策略）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from evaluation.qualitative_queries import (
    ALL_QUALITATIVE,
    BULL_QUALITATIVE,
    CHANGXIN_QUALITATIVE,
)

_STORAGE = Path(__file__).resolve().parent.parent / "storage"
_CHUNK_FILES = {
    "公牛集团": _STORAGE / "bull_chunks.json",
    "长鑫科技": _STORAGE / "长鑫科技_chunks.json",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def test_qualitative_scale():
    """定性查询要有统计意义：总量 ≥30，两家公司各占合理比例。"""
    assert len(ALL_QUALITATIVE) >= 30, f"总量 {len(ALL_QUALITATIVE)} 不足 30"
    assert len(BULL_QUALITATIVE) >= 20
    assert len(CHANGXIN_QUALITATIVE) >= 15


def test_qualitative_wellformed():
    """结构合法：qid 唯一、问题与锚点非空、公司/期间字段合法。"""
    qids = [q.qid for q in ALL_QUALITATIVE]
    assert len(qids) == len(set(qids)), f"qid 重复：{[q for q in qids if qids.count(q) > 1]}"
    for q in ALL_QUALITATIVE:
        assert q.query.strip(), f"{q.qid} 缺问题"
        assert q.gold_phrases and all(p.strip() for p in q.gold_phrases), f"{q.qid} 缺锚点"
        assert q.company_name in _CHUNK_FILES, f"{q.qid} 公司未知：{q.company_name}"
        assert q.fiscal_year > 0, f"{q.qid} 期间非法"


@pytest.mark.skipif(
    not all(f.exists() for f in _CHUNK_FILES.values()),
    reason="storage 下财报 chunk json 未切块（storage git 忽略，需先运行切块脚本）",
)
def test_anchors_hit_chunks():
    """每个 gold 锚点必须命中对应公司 chunk 的至少一段（去空白子串匹配）。

    悬空锚点会让该查询的 gold 集合为空 → Recall 恒 0，评测失真。
    """
    texts = {
        company: [x["text"] for x in json.loads(path.read_text(encoding="utf-8"))]
        for company, path in _CHUNK_FILES.items()
    }
    misses: list[str] = []
    for q in ALL_QUALITATIVE:
        for phrase in q.gold_phrases:
            if not any(_norm(phrase) in _norm(t) for t in texts[q.company_name]):
                misses.append(f"{q.qid}: {phrase}")
    assert not misses, f"锚点未命中（{len(misses)} 个，须修正）：\n" + "\n".join(misses)
