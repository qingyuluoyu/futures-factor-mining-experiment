"""A 股因子目录 —— 题目 2 要求的八大类因子

覆盖价值（PE/PB）、成长（营收同比/净利润同比）、质量（ROE/毛利率）、
动量（20日收益率）、波动（20日波动率）、流动性（20日换手率）六类。
每个因子声明方向（direction）：high=值越大越好，low=值越小越好，
IC 符号检验与多空组合方向以此为准。
"""

from __future__ import annotations

from dataclasses import dataclass

FACTOR_POLICY_VERSION = "stock-factor-domain-v1"


def factor_policy(factor_id: str) -> dict[str, str]:
    positive_only = factor_id in {"pe_ttm", "pb"}
    return {
        "policy_version": FACTOR_POLICY_VERSION,
        "eligibility_rule": "finite_strictly_positive" if positive_only else "finite",
        "missing_value_policy": "exclude_without_imputation" if positive_only else "cross_sectional_median_if_enabled",
        "exclusion_rule": "PE/PB非正、缺失或非有限值排除，预处理不得恢复" if positive_only else "保留有限负值；缺失与非有限值按配置处理",
    }


@dataclass(frozen=True)
class StockFactorSpec:
    """因子元数据"""

    id: str
    label: str
    group: str
    formula: str
    direction: str  # high=因子值越大预期收益越高 / low=相反
    source: str  # valuation=每日估值 / financial=季度财报(公告日对齐) / kline=量价
    description: str


FACTOR_SPECS: list[StockFactorSpec] = [
    # ---- 价值 ----
    StockFactorSpec(
        id="pe_ttm",
        label="市盈率 TTM",
        group="价值",
        formula="PE(TTM) = 总市值 / 近四季归母净利润",
        direction="low",
        source="valuation",
        description="估值越低预期收益越高（价值效应）",
    ),
    StockFactorSpec(
        id="pb",
        label="市净率",
        group="价值",
        formula="PB = 股价 / 每股净资产",
        direction="low",
        source="valuation",
        description="账面价值锚定的估值水平",
    ),
    # ---- 成长 ----
    StockFactorSpec(
        id="revenue_yoy",
        label="营收同比",
        group="成长",
        formula="营业总收入同比增长率（%）",
        direction="high",
        source="financial",
        description="收入端成长性",
    ),
    StockFactorSpec(
        id="netprofit_yoy",
        label="净利润同比",
        group="成长",
        formula="归母净利润同比增长率（%）",
        direction="high",
        source="financial",
        description="利润端成长性",
    ),
    # ---- 质量 ----
    StockFactorSpec(
        id="roe",
        label="ROE",
        group="质量",
        formula="净资产收益率（%）",
        direction="high",
        source="financial",
        description="股东回报率，盈利质量核心",
    ),
    StockFactorSpec(
        id="gross_margin",
        label="毛利率",
        group="质量",
        formula="销售毛利率（%）",
        direction="high",
        source="financial",
        description="产品竞争力与定价权",
    ),
    # ---- 动量 ----
    StockFactorSpec(
        id="ret_20d",
        label="20日收益率",
        group="动量",
        formula="close[t] / close[t-20] - 1",
        direction="high",
        source="kline",
        description="过去20个交易日涨跌幅",
    ),
    # ---- 波动 ----
    StockFactorSpec(
        id="vol_20d",
        label="20日波动率",
        group="波动",
        formula="近20日收益率标准差 × √244（年化）",
        direction="low",
        source="kline",
        description="低波异象：低波动股票风险调整后收益更优",
    ),
    # ---- 流动性 ----
    StockFactorSpec(
        id="turnover_20d",
        label="20日换手率",
        group="流动性",
        formula="近20日日均换手率（%）",
        direction="low",
        source="valuation",
        description="低换手（关注度低）历史上有超额收益",
    ),
]

FACTOR_BY_ID = {f.id: f for f in FACTOR_SPECS}

GROUPS = ["价值", "成长", "质量", "动量", "波动", "流动性"]
