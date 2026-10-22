"""集中配置项：环境变量覆盖 + 默认值（惰性读取）。

全部用函数而非模块级常量——每次调用重新读 os.getenv，保证测试里
monkeypatch.setenv 后能取到新值，生产环境改环境变量即可换模型 / 端点 /
规则版本 / 开关，无需改代码（方案 14 章「config/ 配置与规则版本」落地）。
"""

from __future__ import annotations

import os


def llm_model() -> str:
    """LLM 决策层（agent/llm.py）+ 归因结论合成（agent/attribution.py）共用的模型名。"""
    return os.getenv("LLM_MODEL", "deepseek-v4-pro")


def llm_base_url() -> str:
    """DeepSeek OpenAI 兼容端点。"""
    return os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")


def rule_version() -> str:
    """科目映射 / 公式 / 报告的规则版本，写入 AnalysisRun 可追溯（方案第 6 章规则 7）。"""
    return os.getenv("RULE_VERSION", "deterministic-v1")


def enable_reranker() -> bool:
    """Reranker 重排开关（默认关）。

    51 道定性 query 评测：qwen3-rerank 对 Recall@5/MRR 零增益、仅把延迟从 ~14ms
    拖到 ~930ms，故默认关闭，显式 ENABLE_RERANKER=1/true/on 才启用
    （见 evaluation/reranker_diagnosis.py）。
    """
    return os.getenv("ENABLE_RERANKER", "").strip().lower() in ("1", "true", "yes", "on")
