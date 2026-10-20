"""retrieval.embedding —— BGE 语义 embedding（方案 5.11 混合召回）。

BM25 词法召回抓「精确词语」，BGE 语义召回抓「表达不同但意思接近」的文本（方案 5.11
③）。本模块封装 BAAI/bge-small-zh-v1.5：
- 懒加载 SentenceTransformer（首次 encode 才下载/加载，避免初始化耗时）；
- encode_fn 可注入（测试用假向量，不触网）；
- 加载/编码失败返回 None，调用方降级到纯 BM25，绝不因 embedding 不可用而崩溃。
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

# 本地已下载的模型优先（离线、免下载）；不存在则回退远端名（走 HF 镜像自动下载）。
# 本地路径基于 __file__ 定位项目根，不依赖 cwd。
_REMOTE_MODEL = "BAAI/bge-small-zh-v1.5"
_LOCAL_MODEL = str(Path(__file__).resolve().parent.parent / "models" / "bge-small-zh-v1.5")

_DEFAULT_MODEL = _LOCAL_MODEL if Path(_LOCAL_MODEL).is_dir() else _REMOTE_MODEL

# 国内下载走 hf-mirror；OMP 冲突（torch 的 libiomp5md.dll 与 anaconda mkl）规避。
# setdefault 只在不显式设置时生效，用户可用环境变量覆盖。
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


class BGEEmbedder:
    """BGE embedding：懒加载 + 可注入 + 失败降级。"""

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL,
        device: str | None = None,
        encode_fn=None,
    ):
        self.model_name = model_name
        self.device = device
        self._encode_fn = encode_fn  # 测试注入：callable(list[str]) -> np.ndarray
        self._model = None
        self._load_failed = False

    def _load(self) -> None:
        if self._model is not None or self._load_failed:
            return
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name, device=self.device or "cpu")
        except Exception:
            # 模型未下载 / 网络不可达 / 依赖缺失：标记失败，encode 返回 None
            self._load_failed = True

    def encode(self, texts: list[str], normalize: bool = True):
        """把文本列表编码为 (n, dim) 向量；失败返回 None（调用方降级）。"""
        if self._encode_fn is not None:
            result = self._encode_fn(texts)
            return np.asarray(result, dtype="float32") if result is not None else None
        self._load()
        if self._model is None:
            return None
        try:
            vecs = self._model.encode(
                list(texts), normalize_embeddings=normalize, show_progress_bar=False
            )
            return np.asarray(vecs, dtype="float32")
        except Exception:
            return None
