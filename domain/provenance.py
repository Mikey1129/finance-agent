"""Provenance —— 来源与数据血缘。

对应方案第 6 章「数据模型」第 3 类。回答「这个数字从哪来、怎么变的」，
是「来源 100% 可追溯」验收目标的基础。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Provenance(BaseModel):
    """一条财务事实的来源血缘记录。"""

    provenance_id: str = Field(description="全局唯一血缘 ID")
    report_id: str = Field(description="关联 ReportDoc.report_id")

    # 原始位置
    source_file: str | None = Field(default=None, description="原始文件路径/名")
    page: int | None = Field(default=None, description="页码（PDF）")
    table_name: str | None = Field(default=None, description="表格位置/名称")
    cell: str | None = Field(default=None, description="单元格坐标，如 B12")
    row_index: int | None = Field(default=None, description="行号（CSV/表格）")

    # 提取方式与规则版本
    extraction_method: str | None = Field(
        default=None, description="提取方法：excel_cell / pdf_table / pdf_text / ..."
    )
    mapping_rule_version: str | None = Field(default=None, description="科目映射规则版本")

    # 转换步骤（规则 4/7：可追溯每一步做了什么）
    transform_steps: list[str] = Field(
        default_factory=list,
        description="转换步骤，如 ['单位换算:万元→元', '科目映射:主营业务收入→revenue']",
    )

    def add_step(self, step: str) -> None:
        """追加一条转换步骤。"""
        self.transform_steps.append(step)
