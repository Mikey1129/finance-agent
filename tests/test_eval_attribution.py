"""评测 C（证据归因）模块测试：内置场景全通过 + 报告统计正确。"""

from evaluation.eval_attribution import (
    AttributionCase,
    build_cases,
    eval_attribution,
    run_case,
)


def test_build_cases_covers_four_dimensions():
    cases = build_cases()
    names = {c.name for c in cases}
    # 四维度各有对应场景
    assert "refs_traceable_and_complete" in names          # 引用正确
    assert "none_value_fact_ignored" in names              # 不把推测当事实
    assert "contradiction_same_item_disclosed" in names    # 反向证据（冲突）
    assert "source_mismatch_disclosed" in names            # 反向证据（来源不一致）
    assert "attribution_missing_text" in names             # 不超证据范围
    assert "fact_query_missing_fact" in names              # 不超证据范围


def test_all_builtin_cases_pass():
    result = eval_attribution()
    assert result.passed, [
        (c.name, c.failures) for c in result.cases if not c.passed
    ]


def test_failure_is_reported():
    # 故意造一个「期望充分、实际缺事实」的坏场景，验证评测能抓到并报告失败
    bad = AttributionCase(
        name="bad_case",
        query="营收",
        intent=None,  # 无意图 → 不会缺证据，但期望 sufficient=False 会失败
        expect_sufficient=False,
    )
    r = run_case(bad)
    assert not r.passed
    assert any("充分性" in f for f in r.failures)
