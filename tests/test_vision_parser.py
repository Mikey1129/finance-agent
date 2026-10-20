"""vision_parser 视觉解析兜底测试（阶段 5）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from domain import StatementType
from ingestion.vision_parser import (
    QwenVLVisionParser,
    VisionParser,
    is_scanned_pdf,
    parse_pdf_with_fallback,
)

_STORAGE = Path(__file__).resolve().parent.parent / "storage"
_REAL_PDF = _STORAGE / "公牛集团-2024年报.pdf"


def _blank_pdf(tmp_path) -> str:
    """PIL 生成纯图像 PDF（无文本层，模拟扫描件）。"""
    p = tmp_path / "blank.pdf"
    Image.new("RGB", (200, 200), "white").save(p)
    return str(p)


class _FakeVision(VisionParser):
    def __init__(self, text: str = "识别文本"):
        self.text = text
        self.calls = 0

    def parse_page(self, page_image: bytes, prompt: str) -> str:
        self.calls += 1
        return self.text


# -- is_scanned_pdf ---------------------------------------------------------
def test_is_scanned_pdf_true_for_blank(tmp_path):
    assert is_scanned_pdf(_blank_pdf(tmp_path)) is True


@pytest.mark.skipif(not _REAL_PDF.exists(), reason="需真实年报 PDF")
def test_is_scanned_pdf_false_for_text_pdf():
    assert is_scanned_pdf(str(_REAL_PDF)) is False


def test_is_scanned_pdf_false_for_missing():
    assert is_scanned_pdf("/no/such/file.pdf") is False


# -- QwenVLVisionParser -----------------------------------------------------
def test_qwen_vl_parse_page():
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "识别结果"}}]}

    parser = QwenVLVisionParser(api_key="sk-test")
    with patch("httpx.post", return_value=_Resp()) as mock_post:
        result = parser.parse_page(b"fake-image", "请识别")
    assert result == "识别结果"
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
    payload = kwargs["json"]
    assert payload["model"] == "qwen-vl-max"
    img_url = payload["messages"][0]["content"][1]["image_url"]["url"]
    assert img_url.startswith("data:image/png;base64,")


# -- parse_pdf_with_fallback ------------------------------------------------
def test_fallback_uses_vision_for_scanned(tmp_path):
    vision = _FakeVision()
    result = parse_pdf_with_fallback(
        _blank_pdf(tmp_path), "测试公司", 2024, StatementType.BALANCE_SHEET, vision_parser=vision
    )
    assert vision.calls >= 1
    assert result.pages and all(p.text == "识别文本" for p in result.pages)


@pytest.mark.skipif(not _REAL_PDF.exists(), reason="需真实年报 PDF")
def test_fallback_uses_pdfplumber_for_text_pdf():
    vision = _FakeVision()
    result = parse_pdf_with_fallback(
        str(_REAL_PDF), "公牛集团", 2024, StatementType.BALANCE_SHEET, vision_parser=vision
    )
    assert vision.calls == 0  # 有文本层，不调视觉
    assert result.facts  # pdfplumber 正常解析


def test_fallback_no_vision_uses_pdfplumber(tmp_path):
    """未配置 vision_parser 时行为不变（即使扫描件也走 pdfplumber）。"""
    result = parse_pdf_with_fallback(
        _blank_pdf(tmp_path), "测试公司", 2024, StatementType.BALANCE_SHEET
    )
    # 无视觉兜底 → 走 pdfplumber，扫描件提取不到 facts（空），但不报错
    assert result.facts == []
