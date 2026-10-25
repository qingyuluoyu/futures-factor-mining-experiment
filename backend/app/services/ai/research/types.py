"""多品种调研类型定义 —— 子代理信号与批次结构"""

from __future__ import annotations

from typing import Literal, TypedDict


Bias = Literal["long", "short", "range"]
SubAgentStatus = Literal["pending", "running", "done", "error"]


class ProductSignal(TypedDict):
    """单品种技术面信号（含可操作点位）"""

    symbol: str
    product_code: str
    product_name: str
    bias: Bias
    # 中文方向：做多 / 做空 / 震荡
    direction_zh: str
    # 操作：买入开多 / 卖出开空 / 观望
    action: str
    confidence: float
    last_price: float | None
    change_pct: float | None
    # 可操作点位（震荡时可为 null）
    entry: float | None
    support: float | None
    resistance: float | None
    stop_loss: float | None
    take_profit: float | None
    # 兼容旧字段名
    stop: float | None
    target: float | None
    risk_reward: float | None
    reasons: list[str]
    as_of: str | None


class ProductRef(TypedDict):
    """待调研品种引用"""

    product_code: str
    symbol: str
    product_name: str


class SubAgentPlanItem(TypedDict):
    """子代理计划项（前端列表）"""

    id: str
    name: str
    symbols: list[str]
    product_codes: list[str]
    status: SubAgentStatus


class BatchResult(TypedDict):
    """单批子代理结果"""

    agent_id: str
    agent_name: str
    signals: list[ProductSignal]
    long_count: int
    short_count: int
    range_count: int
    error_count: int
    error: str | None


class ResearchSummary(TypedDict):
    """调研汇总（写入 tool_result，供主代理汇报）"""

    success: bool
    total_products: int
    long: list[ProductSignal]
    short: list[ProductSignal]
    range_filtered: int
    errors: list[str]
    agent_count: int
    note: str
    # 预渲染 Markdown，主代理应优先照抄表格中的数字
    report_markdown: str
