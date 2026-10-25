"""内置 Agent 工具的 ToolDef 列表"""

from __future__ import annotations

from app.services.ai.providers.base import ToolDef
from app.services.ai.tools.paper import get_paper_trade_tool_definitions


def get_tool_definitions() -> list[ToolDef]:
    """预定义内置工具列表（供 Provider function calling）。"""
    tools: list[ToolDef] = [
        ToolDef(
            name="get_quote",
            description=(
                "获取期货合约实时行情。"
                "返回最新价、涨跌、开高低、昨结算(pre_settle)、量、持仓量(open_interest)、买卖一。"
                "参数 symbol 如 rb2610。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "合约代码，如 rb2610",
                    },
                },
                "required": ["symbol"],
            },
        ),
        ToolDef(
            name="get_kline",
            description=(
                "获取期货 K 线 OHLCV + 结算价/持仓量（若有）。"
                "周期 period: 1m/5m/15m/30m/60m/1d；limit 默认 50，最大 200。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "合约代码"},
                    "period": {
                        "type": "string",
                        "description": "K线周期",
                        "enum": ["1m", "5m", "15m", "30m", "60m", "1d"],
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回最近 N 根，默认 50，最大 200",
                    },
                },
                "required": ["symbol", "period"],
            },
        ),
        ToolDef(
            name="get_open_interest_series",
            description=(
                "获取成交量/持仓量/结算价时间序列，用于分析增仓/减仓、量仓配合。"
                "基于 K 线字段抽取。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "合约代码"},
                    "period": {
                        "type": "string",
                        "enum": ["1m", "5m", "15m", "30m", "60m", "1d"],
                        "description": "周期，默认 1d 更适合持仓分析",
                    },
                    "limit": {"type": "integer", "description": "点数，默认 50"},
                },
                "required": ["symbol"],
            },
        ),
        ToolDef(
            name="get_master_contract",
            description=(
                "查询品种主力/次主力合约。参数 code 为品种代码如 RB、AU、IF。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "品种代码，如 RB",
                    },
                },
                "required": ["code"],
            },
        ),
        ToolDef(
            name="get_market_snapshot",
            description=(
                "一次获取分析快照：实时行情 + 最近 K 线摘要/尾部 bars + 主力信息。"
                "适合先做总体判断再深入指标。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "合约代码"},
                    "period": {
                        "type": "string",
                        "enum": ["1m", "5m", "15m", "30m", "60m", "1d"],
                        "description": "K线周期，默认 5m",
                    },
                    "kline_limit": {
                        "type": "integer",
                        "description": "K线数量，默认 50",
                    },
                },
                "required": ["symbol"],
            },
        ),
        ToolDef(
            name="get_news",
            description="获取期货品种相关最新新闻。参数 symbol 合约代码。",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "合约代码"},
                },
                "required": ["symbol"],
            },
        ),
        ToolDef(
            name="get_big_orders",
            description=(
                "查询某合约今日大单成交统计（多空大单量对比+Top大单列表）。"
                "min_volume 为大单手数门槛，默认 100，活跃品种建议 200-500。"
                "适合分析主力资金流向与大单异动。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "合约代码，如 rb2610"},
                    "min_volume": {
                        "type": "integer",
                        "description": "大单手数门槛，默认 100",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多取多少笔逐笔成交参与统计，默认 2000",
                    },
                },
                "required": ["symbol"],
            },
        ),
        ToolDef(
            name="get_orderbook",
            description=(
                "获取某合约盘口数据：买卖五档挂单量 + 最新价/涨跌/成交量/"
                "持仓量/内外盘等统计。适合分析即时买卖力量与盘口异动。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "合约代码，如 rb2610"},
                },
                "required": ["symbol"],
            },
        ),
        ToolDef(
            name="calculate_indicator",
            description=(
                "计算技术指标并返回最近点与信号。"
                "indicator: MA/EMA/MACD/RSI/BOLL/KDJ/VOLUME。"
                "params 示例: MA {period:20}; MACD {fast:12,slow:26,signal:9};"
                " RSI {period:14}; BOLL {period:20,std:2}; KDJ {n:9,k:3,d:3};"
                " VOLUME {ma_period:5}。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "合约代码"},
                    "indicator": {
                        "type": "string",
                        "enum": [
                            "MA",
                            "EMA",
                            "MACD",
                            "RSI",
                            "BOLL",
                            "KDJ",
                            "VOLUME",
                        ],
                        "description": "指标名称",
                    },
                    "period": {
                        "type": "string",
                        "enum": ["1m", "5m", "15m", "30m", "60m", "1d"],
                        "description": "K线周期，默认 5m",
                    },
                    "params": {"type": "object", "description": "指标参数"},
                    "lookback": {
                        "type": "integer",
                        "description": "用于计算的 K 线根数，默认 120，最大 200",
                    },
                    "recent": {
                        "type": "integer",
                        "description": "返回最近 N 个有效点，默认 5，最大 30",
                    },
                },
                "required": ["symbol", "indicator"],
            },
        ),
        ToolDef(
            name="get_indicator_settings",
            description=(
                "读取用户当前 K 线图表指标设置（均线/MACD/RSI/布林开关、参数、颜色、线型）。"
                "改设置前可选调用。与 calculate_indicator（算数值）不同。"
            ),
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        ToolDef(
            name="update_indicator_settings",
            description=(
                "【图表显示设置】立即修改用户 K 线图上的指标显示，无需合约代码。"
                "用户说打开/关闭/改参数/改颜色/改实线虚线时必须用本工具，"
                "不要问合约、不要用 calculate_indicator。"
                "reset=true 恢复默认；或传 maLines/macd/rsi/boll 补丁。"
                "开RSI: {\"rsi\":{\"enabled\":true,\"period\":14}}；"
                "关MACD: {\"macd\":{\"enabled\":false}}；"
                "布林实线上轨: {\"boll\":{\"enabled\":true,\"upperStyle\":\"solid\"}}；"
                "均线: {\"maLines\":[{\"period\":5,\"color\":\"#3b82f6\"},"
                "{\"period\":10,\"color\":\"#f59e0b\"},{\"period\":20,\"color\":\"#a855f7\"}]}。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reset": {
                        "type": "boolean",
                        "description": "true 时恢复默认配置",
                    },
                    "config": {
                        "type": "object",
                        "description": "完整或部分 IndicatorConfig",
                    },
                    "maLines": {
                        "type": "array",
                        "description": "均线列表，最多5条 {period,color}",
                    },
                    "macd": {"type": "object", "description": "MACD 配置补丁"},
                    "rsi": {"type": "object", "description": "RSI 配置补丁"},
                    "boll": {
                        "type": "object",
                        "description": "布林带配置；线型 solid/dashed",
                    },
                },
                "required": [],
            },
        ),
        ToolDef(
            name="code_sandbox",
            description="在沙箱中执行 Python 代码。参数 code 为代码字符串。",
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 代码",
                    },
                },
                "required": ["code"],
            },
            requires_confirmation=True,
        ),
        ToolDef(
            name="research_products",
            description=(
                "【多品种/全市场调研】并行子代理扫描主力技术面，"
                "返回明确做多/做空及具体数字点位："
                "现价、入场点、支撑位、压力位、止损、止盈、盈亏比；"
                "并带 report_markdown 表格。震荡自动过滤。"
                "用户要全市场/多品种买卖点位时必须用本工具，禁止逐个 get_quote 扫全市场。"
                "回答用户时优先原样展示 report_markdown，勿改成模糊表述。"
                "scope=\"all\" 或品种数组；period 推荐 1d；batch_size 5-10。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "scope": {
                        "description": (
                            '\"all\" 或品种/合约代码数组，如 [\"RB\",\"AU\",\"IF\"]'
                        ),
                        "anyOf": [
                            {"type": "string"},
                            {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        ],
                    },
                    "period": {
                        "type": "string",
                        "enum": ["1d", "60m", "30m", "15m", "5m"],
                        "description": "技术面周期，默认 1d",
                    },
                    "batch_size": {
                        "type": "integer",
                        "description": "每子代理品种数 5-10，默认 8",
                    },
                    "max_products": {
                        "type": "integer",
                        "description": "最多调研品种数，默认 120",
                    },
                },
                "required": [],
            },
        ),
    ]
    tools.extend(get_paper_trade_tool_definitions())
    return tools


def get_tool_map() -> dict[str, ToolDef]:
    """工具名 → 定义映射。"""
    return {tool.name: tool for tool in get_tool_definitions()}
