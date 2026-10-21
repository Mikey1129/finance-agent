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
    # 美元复合单位（海外财报）
    assert unit_scale("美元") == 1.0
    assert unit_scale("千美元") == 1e3
    assert unit_scale("万美元") == 1e4
    assert unit_scale("百万美元") == 1e6
    assert unit_scale("亿美元") == 1e8


def test_convert_to_yuan():
    assert convert_to_yuan(12.34, "万元") == 123400.0
    assert convert_to_yuan(1.5, "亿元") == 150000000.0
    assert convert_to_yuan(5, "元") == 5.0


def test_item_zh():
    assert item_zh("revenue") == "营业收入"
    assert item_zh("net_profit") == "净利润"


def test_map_item_strips_op_prefix():
    """利润表/现金流量表行首运算符号前缀应被剥离。"""
    assert map_item("减：营业外支出") == "non_operating_expenses"
    assert map_item("减：所得税费用") == "income_tax_expense"
    assert map_item("加：营业外收入") == "non_operating_income"
    assert map_item("加：其他收益") == "other_income"
    assert map_item("其中：营业收入") == "revenue"


def test_map_item_strips_number_prefix():
    """附注表数字序号前缀应被剥离。"""
    assert map_item("2.应收款项融资") == "financing_receivables"
    assert map_item("1.归属于母公司股东的净利润（净亏损以“-”号填列）") == "net_profit_parent"


def test_map_item_strips_cn_number_prefix():
    """中文序号前缀（一、/二、/五、）应被剥离。"""
    assert map_item("一、营业总收入") == "revenue"
    assert map_item("一、营业收入") == "revenue"
    assert map_item("二、营业利润（亏损以“－”号填列）") == "operating_profit"
    assert map_item("三、利润总额（亏损总额以“－”号填列）") == "total_profit"
    assert map_item("五、净利润（净亏损以“－”号填列）") == "net_profit"
    assert map_item("五、现金及现金等价物净增加额") == "net_change_in_cash"


def test_map_item_prefix_strip_does_not_overreach():
    """剥离前缀后仍不命中的科目，应回退归一化原名，不被误映射。"""
    assert map_item("加：会计政策变更") == "加:会计政策变更"
    assert map_item("其中：数据资源") == "其中:数据资源"


def test_map_item_new_income_items():
    """补充的利润表标准科目。"""
    assert map_item("其他收益") == "other_income"
    assert map_item("利息费用") == "interest_expense"
    assert map_item("信用减值损失（损失以“-”号填列）") == "credit_impairment_loss"
    assert map_item("资产减值损失") == "asset_impairment_loss"
    assert map_item("基本每股收益(元/股)") == "basic_eps"
    assert map_item("稀释每股收益") == "diluted_eps"


def test_map_item_cost_items_not_confused():
    """营业成本与营业总成本是不同科目，不能互相污染（否则毛利率算错）。"""
    assert map_item("营业成本") == "cost_of_revenue"
    assert map_item("营业总成本") == "total_operating_costs"
    assert map_item("二、营业总成本") == "total_operating_costs"
    assert map_item("其中：营业成本") == "cost_of_revenue"
