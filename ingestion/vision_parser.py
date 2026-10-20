"""ingestion.vision_parser —— 视觉解析兜底（Qwen-VL，阶段 5）。

处理 pdfplumber 无法解析的 PDF：扫描件（无文本层）或复杂版面。视觉模型把
页面图像识别为文本，再组装为 PageContent（供 chunker 切块检索）。

设计要点（方案 15「pdfplumber 规则主体 + Qwen-VL 视觉兜底」）：
- `VisionParser` 是抽象接口，`QwenVLVisionParser` 是 DashScope 实现（测试用 mock）；
- `is_scanned_pdf` 检测无文本层 PDF，决定是否走视觉兜底；
- 视觉兜底是「可选增强」：未配置 vision_parser 时行为不变（仅 pdfplumber）。
"""

from __future__ import annotations

import base64
import io
from abc import ABC, abstractmethod

import pdfplumber

from domain import StatementType
from ingestion.excel_parser import build_report_doc
from ingestion.pdf_parser import PageContent, PdfParseResult, parse_pdf

# 三大报表视觉识别提示词中的中文名
_STATEMENT_NAMES = {
    StatementType.BALANCE_SHEET: "资产负债表",
    StatementType.INCOME_STATEMENT: "利润表",
    StatementType.CASH_FLOW: "现金流量表",
}


class VisionParser(ABC):
    """视觉解析接口：把一页 PDF 图像识别为文本（含表格结构）。"""

    @abstractmethod
    def parse_page(self, page_image: bytes, prompt: str) -> str:
        """输入页面图像字节（PNG）+ 提示，返回该页识别出的文本/表格。"""


class QwenVLVisionParser(VisionParser):
    """DashScope Qwen-VL 视觉解析（OpenAI 兼容多模态接口）。"""

    def __init__(
        self,
        api_key: str,
        model: str = "qwen-vl-max",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def parse_page(self, page_image: bytes, prompt: str) -> str:
        import httpx

        b64 = base64.b64encode(page_image).decode()
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
        }
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def is_scanned_pdf(path: str, min_text_chars: int = 20) -> bool:
    """判断 PDF 是否无文本层（扫描件）：前 3 页文本总量过少即视为扫描件。

    打不开的文件不判定为扫描件（返回 False），交给 parse_pdf 抛错。
    """
    try:
        with pdfplumber.open(path) as pdf:
            total = sum(
                len((page.extract_text() or "").strip()) for page in list(pdf.pages)[:3]
            )
            return total < min_text_chars
    except Exception:
        return False


def _build_prompt(statement_type: StatementType, fiscal_year: int) -> str:
    name = _STATEMENT_NAMES.get(statement_type, "财务报表")
    return (
        f"这是 {fiscal_year} 年度财务报告的{name}页面扫描件。"
        "请识别页面中所有文字和表格数据，表格按行列输出，用竖线 | 分隔单元格，"
        "保留数字、单位（元/千元/万元/亿元）与负数括号。不要编造缺失内容。"
    )


def _render_pages(path: str, scale: float = 2.0) -> list[bytes]:
    """用 pypdfium2 把 PDF 每页渲染成 PNG 字节。"""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    images: list[bytes] = []
    try:
        for page in pdf:
            bitmap = page.render(scale=scale)
            try:
                pil = bitmap.to_pil()
                buf = io.BytesIO()
                pil.save(buf, format="PNG")
                images.append(buf.getvalue())
            finally:
                bitmap.close()
    finally:
        pdf.close()
    return images


def _vision_parse(
    path: str,
    company_name: str,
    fiscal_year: int,
    statement_type: StatementType,
    vision_parser: VisionParser,
    currency: str = "CNY",
) -> PdfParseResult:
    """视觉兜底：逐页渲染图像 → 视觉识别文本 → 组装 PageContent（供切块检索）。

    视觉识别只产出文本（facts 的精确提取留待视觉输出结构化后接入 parse_tabular），
    故 facts/provenance 为空——扫描件兜底的目标是「恢复可检索文本」。
    """
    report_doc = build_report_doc(path, company_name, fiscal_year, statement_type)
    prompt = _build_prompt(statement_type, fiscal_year)
    pages: list[PageContent] = []
    for i, image in enumerate(_render_pages(path), start=1):
        text = vision_parser.parse_page(image, prompt)
        pages.append(PageContent(page_number=i, text=text))
    return PdfParseResult(report_doc=report_doc, pages=pages, facts=[], provenance=[])


def parse_pdf_with_fallback(
    path: str,
    company_name: str,
    fiscal_year: int,
    statement_type: StatementType,
    vision_parser: VisionParser | None = None,
    currency: str = "CNY",
) -> PdfParseResult:
    """pdfplumber 优先；扫描件（无文本层）且配置了 vision_parser 时视觉兜底。"""
    if vision_parser is not None and is_scanned_pdf(path):
        return _vision_parse(path, company_name, fiscal_year, statement_type, vision_parser, currency)
    return parse_pdf(path, company_name, fiscal_year, statement_type, currency)
