"""ingestion.vision_parser —— 视觉解析兜底（Qwen3-VL，阶段 5）。

处理 pdfplumber 无法解析的 PDF：扫描件（无文本层）或复杂版面。视觉模型把
页面图像识别为结构化表格，再复用 `parse_tabular` 提取 FinancialFact——
视觉兜底不只恢复文本，还要能查数值、算指标。

设计要点（方案 15「pdfplumber 规则主体 + Qwen-VL 视觉兜底」）：
- `VisionParser` 是抽象接口，`QwenVLVisionParser` 是 DashScope 实现（测试用 mock）；
- `is_scanned_pdf` 检测无文本层 PDF，决定是否走视觉兜底；
- `build_vision_parser()` 从环境变量读 key 构造 parser（有 key 才启用，否则 None）；
- 视觉识别输出「标题 + 单位 + markdown 表格」，复用 `_match_title` 定位报表范围、
  复用 `parse_tabular` 提取 facts（与 pdfplumber 路径同一套科目映射/期间/单位逻辑）。
"""

from __future__ import annotations

import base64
import io
import os
import re
from abc import ABC, abstractmethod

import pdfplumber

from domain import FinancialFact, Provenance, ReportDoc, StatementType
from finance.taxonomy import RULES_VERSION
from ingestion.excel_parser import build_report_doc, detect_unit, parse_tabular
from ingestion.pdf_parser import PageContent, PdfParseResult, _match_title, parse_pdf

# 三大报表视觉识别提示词中的中文名
_STATEMENT_NAMES = {
    StatementType.BALANCE_SHEET: "资产负债表",
    StatementType.INCOME_STATEMENT: "利润表",
    StatementType.CASH_FLOW: "现金流量表",
}


class VisionParser(ABC):
    """视觉解析接口：把一页 PDF 图像识别为结构化表格文本（含标题/单位）。"""

    @abstractmethod
    def parse_page(self, page_image: bytes, prompt: str) -> str:
        """输入页面图像字节（PNG）+ 提示，返回该页识别出的结构化文本。"""


class QwenVLVisionParser(VisionParser):
    """DashScope Qwen3-VL 视觉解析（OpenAI 兼容多模态接口）。

    `vl_high_resolution_images=True` 把像素上限提到 16384 token，专治财报
    密集小字/表格 OCR（默认关闭时大图会被压缩，数字识别易错）。
    """

    def __init__(
        self,
        api_key: str,
        model: str = "qwen3-vl-plus",
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
            # 财报表格密集小字，提像素上限防压缩导致数字识别错
            "vl_high_resolution_images": True,
        }
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def build_vision_parser() -> VisionParser | None:
    """有视觉 key 时返回可用的 QwenVLVisionParser，否则 None（无视觉兜底）。

    key 优先读 `DASHSCOPE_VL_API_KEY`（视觉专用），未设则回退 `DASHSCOPE_API_KEY`
    （与 Reranker 共用）；模型名可用 `QWEN_VL_MODEL` 覆盖，默认 qwen3-vl-plus。
    """
    key = os.getenv("DASHSCOPE_VL_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    if not key:
        return None
    return QwenVLVisionParser(api_key=key, model=os.getenv("QWEN_VL_MODEL", "qwen3-vl-plus"))


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
        "请识别页面中的财务报表表格，严格按以下格式输出：\n"
        "1. 若页面有报表标题（如「合并资产负债表」「母公司利润表」「合并现金流量表」），"
        "先输出一行「标题: <标题>」；\n"
        "2. 若有「单位：xxx」标注，输出一行「单位: <xxx>」；\n"
        "3. 用 markdown 表格输出报表数据：第一行是表头（第一列科目名，后续列是各期间），"
        "之后每行是一行数据。数字原样保留（含千分位逗号），负数用括号表示，"
        "空值留空，不要编造缺失内容。"
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


def _kv_value(s: str) -> str | None:
    """从「标签: 值」/「标签：值」行提取值；无冒号返回 None。"""
    for sep in (":", "："):
        if sep in s:
            return s.split(sep, 1)[1].strip()
    return None


def _parse_markdown_table(lines: list[str]) -> tuple[list | None, list[list]]:
    """把 markdown 表格行解析为 (表头, 数据行)，跳过 |---|---| 分隔行。"""
    if not lines:
        return None, []

    def cells(line: str) -> list[str]:
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    grid = [cells(l) for l in lines]
    # 去掉分隔行（单元格全由 - : 空格组成）
    grid = [g for g in grid if not all(c == "" or set(c) <= set("-: ") for c in g)]
    if not grid:
        return None, []
    return grid[0], grid[1:]


def _parse_vision_output(text: str) -> tuple[str | None, str | None, list | None, list[list]]:
    """解析 Qwen-VL 输出 → (标题, 单位, 表头, 数据行)。"""
    title: str | None = None
    unit: str | None = None
    table_lines: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("标题"):
            title = _kv_value(s) or None
            continue
        if s.startswith("单位"):
            unit = detect_unit(_kv_value(s) or "") or unit
            continue
        if s.startswith("|"):
            table_lines.append(s)
    headers, rows = _parse_markdown_table(table_lines)
    return title, unit, headers, rows


def _vision_parse(
    path: str,
    company_name: str,
    fiscal_year: int,
    statement_type: StatementType,
    vision_parser: VisionParser,
    currency: str = "CNY",
) -> PdfParseResult:
    """视觉兜底：逐页渲染 → 视觉识别结构化表格 → parse_tabular 提取 facts。

    跨页状态与 pdfplumber 路径一致：标题定位报表类型/合并范围，续页（无表头）
    继承上一表头，跨页行偏移计入 fact_id（防主键冲突）。
    """
    report_doc = build_report_doc(path, company_name, fiscal_year, statement_type)
    prompt = _build_prompt(statement_type, fiscal_year)
    pages: list[PageContent] = []
    facts: list[FinancialFact] = []
    provenance: list[Provenance] = []

    current_scope = "consolidated"
    last_headers: list | None = None
    current_unit: str | None = None
    current_row_offset = 0

    for i, image in enumerate(_render_pages(path), start=1):
        text = vision_parser.parse_page(image, prompt)
        title, unit, headers, rows = _parse_vision_output(text)
        pages.append(PageContent(page_number=i, text=text))

        # 标题 → 报表类型/合并范围；新报表开始重置跨页状态
        if title:
            m = _match_title(title)
            if m is not None:
                _st, scope = m
                if scope is not None:
                    current_scope = scope
                    last_headers = None
                    current_unit = None
                    current_row_offset = 0
        if unit:
            current_unit = unit

        if headers:
            last_headers = headers
            data_rows = rows
        else:
            if last_headers is None:
                continue  # 无表头可继承，跳过
            data_rows = rows
        if not data_rows:
            continue

        fs = parse_tabular(
            last_headers,
            data_rows,
            company_name,
            fiscal_year,
            statement_type,
            report_doc.report_id,
            unit=current_unit,
            consolidation_scope=current_scope,
            source_file=path,
            row_offset=current_row_offset,
        )
        current_row_offset += len(data_rows)
        for f in fs:
            f.provenance_id = f"prov-{f.fact_id}"
            provenance.append(
                Provenance(
                    provenance_id=f.provenance_id,
                    report_id=report_doc.report_id,
                    source_file=path,
                    page=i,
                    extraction_method="vision_ocr",
                    mapping_rule_version=RULES_VERSION,
                )
            )
            facts.append(f)

    return PdfParseResult(report_doc=report_doc, pages=pages, facts=facts, provenance=provenance)


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
