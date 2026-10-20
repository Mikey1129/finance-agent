"""端到端回归测试：合成财报 CSV → 解析 → 入库 → 查询 → 校验。"""

import csv

from domain import StatementType
from finance.store import Store
from finance.validation import check_balance_sheet
from ingestion.excel_parser import parse_csv


def _write_balance_sheet_csv(path) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["项目", "2025年12月31日", "2024年12月31日"])
        w.writerow(["货币资金", "5000", "4000"])
        w.writerow(["应收账款", "3000", "2500"])
        w.writerow(["资产总计", "10000", "8000"])
        w.writerow(["负债合计", "6000", "5000"])
        w.writerow(["所有者权益合计", "4000", "3000"])


def test_end_to_end_csv_to_validation(tmp_path):
    csv_path = tmp_path / "balance_sheet.csv"
    _write_balance_sheet_csv(csv_path)

    # 1. 解析
    result = parse_csv(
        str(csv_path), "测试公司", 2025, StatementType.BALANCE_SHEET, unit="万元"
    )
    assert result.report_doc.report_id == "测试公司-2025-balance_sheet"
    assert len(result.facts) == 10  # 5 行 × 2 期

    # 2. 入库
    store = Store(":memory:")
    store.insert_report_doc(result.report_doc)
    store.insert_financial_facts(result.facts)
    assert store.count_facts() == 10

    # 3. 查询（验证单位换算：万元 → 元）
    assets = store.query_facts(standard_item="total_assets")
    assert assets[0]["value"] == 10000 * 1e4

    # 4. 校验（资产负债表平衡）
    report = check_balance_sheet(result.facts)
    assert report.ok()

    # 5. 来源血缘已关联
    assert all(f.provenance_id for f in result.facts)
    store.close()
