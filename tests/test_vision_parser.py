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
    _parse_vision_output,
    build_vision_parser,
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
    assert payload["model"] == "qwen3-vl-plus"
    # 财报小字表格 OCR：提像素上限防数字识别错
    assert payload["vl_high_resolution_images"] is True
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


# -- _parse_vision_output ----------------------------------------------------
def test_parse_vision_output_markdown_table():
    """解析 Qwen-VL 输出：标题/单位/markdown 表格 → (标题, 单位, 表头, 数据行)。"""
    text = (
        "标题: 合并资产负债表\n"
        "单位: 元\n"
        "| 项目 | 附注 | 2024年12月31日 | 2023年12月31日 |\n"
        "|---|---|---|---|\n"
        "| 货币资金 |  | 5,020,380,352.96 | 4,743,714,425.66 |\n"
        "| 资产总计 |  | 20,494,034,490.68 | 19,762,205,724.93 |\n"
    )
    title, unit, headers, rows = _parse_vision_output(text)
    assert title == "合并资产负债表"
    assert unit == "元"
    assert headers == ["项目", "附注", "2024年12月31日", "2023年12月31日"]
    assert rows == [
        ["货币资金", "", "5,020,380,352.96", "4,743,714,425.66"],
        ["资产总计", "", "20,494,034,490.68", "19,762,205,724.93"],
    ]


def test_parse_vision_output_no_table():
    """纯文本无表格 → 标题/单位/表头/数据行均为空。"""
    title, unit, headers, rows = _parse_vision_output("识别文本")
    assert (title, unit, headers, rows) == (None, None, None, [])


# -- _vision_parse 提取 facts -------------------------------------------------
def test_vision_parse_extracts_facts_and_balances(tmp_path):
    """视觉兜底端到端：两页扫描件 → 视觉文本 → parse_tabular 提取 facts → 资产负债表平衡。"""
    from finance.validation import check_balance_sheet

    # 造两页无文本层扫描件
    scan = tmp_path / "scan2.pdf"
    img1 = Image.new("RGB", (200, 200), "white")
    img2 = Image.new("RGB", (200, 200), "white")
    img1.save(scan, "PDF", save_all=True, append_images=[img2])

    class _SeqVision(VisionParser):
        def __init__(self, texts):
            self.texts = texts
            self.calls = 0

        def parse_page(self, page_image: bytes, prompt: str) -> str:
            t = self.texts[self.calls]
            self.calls += 1
            return t

    vision = _SeqVision(
        [
            # 第 1 页：资产侧
            "标题: 合并资产负债表\n单位: 元\n"
            "| 项目 | 2025年12月31日 |\n|---|---|\n"
            "| 货币资金 | 100 |\n| 资产总计 | 300 |\n",
            # 第 2 页：负债及所有者权益侧（续页，复用表头结构）
            "| 项目 | 2025年12月31日 |\n|---|---|\n"
            "| 负债合计 | 200 |\n| 所有者权益合计 | 100 |\n",
        ]
    )
    result = parse_pdf_with_fallback(
        str(scan), "测试公司", 2025, StatementType.BALANCE_SHEET, vision_parser=vision
    )

    by_item = {f.standard_item: f.value for f in result.facts}
    assert by_item["cash_and_equivalents"] == 100.0
    assert by_item["total_assets"] == 300.0
    assert by_item["total_liabilities"] == 200.0
    assert by_item["total_equity"] == 100.0
    # 来源血缘：视觉提取方法 + provenance 关联
    assert all(f.provenance_id for f in result.facts)
    assert {p.extraction_method for p in result.provenance} == {"vision_ocr"}
    # 资产负债表平衡
    assert check_balance_sheet(result.facts).ok()


def test_build_vision_parser_returns_none_without_key(monkeypatch):
    """无视觉 key → build_vision_parser 返回 None（无视觉兜底，行为不变）。"""
    monkeypatch.delenv("DASHSCOPE_VL_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    assert build_vision_parser() is None
