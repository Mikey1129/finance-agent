"""评测 D（系统安全）测试：内置安全场景全通过 + 六类覆盖齐全。"""

from evaluation.eval_security import run_security_eval


def test_all_security_cases_pass():
    result = run_security_eval()
    assert result.passed, [
        (c.category, c.name, c.detail) for c in result.cases if not c.passed
    ]


def test_security_cases_cover_six_categories():
    result = run_security_eval()
    categories = {c.category for c in result.cases}
    assert categories == {
        "恶意文档", "提示词注入", "参数越界", "预算/循环终止", "错误路由", "记忆污染",
    }
