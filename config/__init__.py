"""config —— 集中配置（方案 14 章「config/ 配置与规则版本」）。

消除散落在 agent/、retrieval/ 的硬编码默认值（model 名 / 端点 / 规则版本 / 开关），
统一「环境变量覆盖 + 默认值」的惰性读取。见 config.settings 各函数说明。
"""

from config.settings import enable_reranker, llm_base_url, llm_model, rule_version

__all__ = ["enable_reranker", "llm_base_url", "llm_model", "rule_version"]
