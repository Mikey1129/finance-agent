"""taxonomy 科目映射与单位换算测试。"""

from finance.taxonomy import convert_to_yuan, item_zh, map_item, unit_scale


def test_map_item_known():
    assert map_item("营业收入") == "revenue"
    assert map_item("净利润") == "net_profit"
    assert map_item("总资产") == "total_assets"
    assert map_item("经营活动产生的现金流量净额") == "operating_cash_flow"


def test_map_item_english():
    assert map_item("revenue") == "revenue"
    assert map_item("Net Income") == "net_profit"


def test_map_item_unknown_returns_normalized():
    assert map_item("某个冷门科目") == "某个冷门科目"


def test_unit_scale():
    assert unit_scale("元") == 1.0
    assert unit_scale("千元") == 1e3
    assert unit_scale("万元") == 1e4
    assert unit_scale("亿元") == 1e8
    assert unit_scale(None) == 1.0
    assert unit_scale("millions") == 1e6


def test_convert_to_yuan():
    assert convert_to_yuan(12.34, "万元") == 123400.0
    assert convert_to_yuan(1.5, "亿元") == 150000000.0
    assert convert_to_yuan(5, "元") == 5.0


def test_item_zh():
    assert item_zh("revenue") == "营业收入"
    assert item_zh("net_profit") == "净利润"
