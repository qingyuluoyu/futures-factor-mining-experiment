"""内置 AI 工具目录 —— 中文名称/简介（设置页展示用）

与 definitions 中的 function-calling 描述分离：
- definitions：给模型看的工具协议
- catalog：给用户看的中文说明 + 分类
"""

from __future__ import annotations

from typing import TypedDict

from app.services.ai.tools.definitions import get_tool_definitions


class ToolCatalogItem(TypedDict):
    """工具目录项"""

    name: str
    name_zh: str
    description_zh: str
    category: str
    requires_confirmation: bool


# name → 中文元数据
_CATALOG: dict[str, dict[str, str]] = {
    "get_quote": {
        "name_zh": "实时行情",
        "description_zh": "查询期货合约最新价、涨跌、开高低、结算价、成交量、持仓量及买卖一价。",
        "category": "行情数据",
    },
    "get_kline": {
        "name_zh": "K 线数据",
        "description_zh": "获取合约历史/近期 K 线（OHLCV，含结算价与持仓量），支持 1 分钟到日线周期。",
        "category": "行情数据",
    },
    "get_open_interest_series": {
        "name_zh": "量仓序列",
        "description_zh": "提取成交量、持仓量、结算价时间序列，便于分析增仓减仓与量仓配合。",
        "category": "行情数据",
    },
    "get_master_contract": {
        "name_zh": "主力合约",
        "description_zh": "按品种代码查询当前主力与次主力合约（如 RB、AU、IF）。",
        "category": "行情数据",
    },
    "get_market_snapshot": {
        "name_zh": "行情快照",
        "description_zh": "一次打包：实时行情 + 最近 K 线摘要 + 主力信息，适合先做总体判断。",
        "category": "行情数据",
    },
    "get_big_orders": {
        "name_zh": "今日大单",
        "description_zh": "查询合约今日大单成交统计：多空大单量对比与最大手数榜单，分析主力资金流向。",
        "category": "行情数据",
    },
    "get_orderbook": {
        "name_zh": "盘口五档",
        "description_zh": "获取合约买卖五档挂单与最新价/成交量/持仓量/内外盘统计，分析即时买卖力量。",
        "category": "行情数据",
    },
    "get_news": {
        "name_zh": "相关新闻",
        "description_zh": "按合约/品种检索最新相关新闻资讯，辅助基本面与事件解读。",
        "category": "资讯",
    },
    "calculate_indicator": {
        "name_zh": "计算技术指标",
        "description_zh": "计算 MA/EMA/MACD/RSI/BOLL/KDJ/VOLUME 等指标数值与信号（不改图表显示）。",
        "category": "技术分析",
    },
    "get_indicator_settings": {
        "name_zh": "读取指标设置",
        "description_zh": "读取您当前 K 线图上的指标开关、参数、颜色与线型配置。",
        "category": "图表设置",
    },
    "update_indicator_settings": {
        "name_zh": "修改指标设置",
        "description_zh": "直接打开/关闭或调整 K 线图指标参数（均线、MACD、RSI、布林、JDK 等）。",
        "category": "图表设置",
    },
    "code_sandbox": {
        "name_zh": "代码沙箱",
        "description_zh": "在隔离环境中执行 Python 代码（需确认），用于计算与验证逻辑。",
        "category": "高级",
    },
    "research_products": {
        "name_zh": "多品种调研",
        "description_zh": (
            "并行子代理扫描主力：输出做多/做空、入场/支撑/压力/止损/止盈具体数字"
            "（震荡过滤）。"
        ),
        "category": "研究",
    },
    "paper_open_long": {
        "name_zh": "做多开仓",
        "description_zh": "模拟买入开多；支持市价立即成交或限价挂单（需确认）。",
        "category": "模拟交易",
    },
    "paper_open_short": {
        "name_zh": "做空开仓",
        "description_zh": "模拟卖出开空；支持市价或限价挂单（需确认）。",
        "category": "模拟交易",
    },
    "paper_close_position": {
        "name_zh": "平仓",
        "description_zh": "按持仓方向平多/平空；市价立即平或限价挂平（需确认）。",
        "category": "模拟交易",
    },
    "paper_place_limit": {
        "name_zh": "限价挂单",
        "description_zh": "限价委托开仓或平仓，到价自动成交（需确认）。",
        "category": "模拟交易",
    },
    "paper_cancel_order": {
        "name_zh": "撤单",
        "description_zh": "撤销尚未成交的挂单（需确认）。",
        "category": "模拟交易",
    },
    "paper_list_positions": {
        "name_zh": "查询持仓",
        "description_zh": "查看当前模拟持仓：方向、均价、手数、保证金等。",
        "category": "模拟交易",
    },
    "paper_list_orders": {
        "name_zh": "查询委托",
        "description_zh": "查看挂单/成交/已撤委托列表，可按状态过滤。",
        "category": "模拟交易",
    },
    "paper_get_account": {
        "name_zh": "查询账户",
        "description_zh": "查看模拟资金：余额、可用/冻结保证金、盈亏等。",
        "category": "模拟交易",
    },
}


def list_tool_catalog() -> list[ToolCatalogItem]:
    """列出全部内置工具目录（中文展示信息）"""
    items: list[ToolCatalogItem] = []
    for tool in get_tool_definitions():
        meta = _CATALOG.get(tool.name, {})
        items.append(
            {
                "name": tool.name,
                "name_zh": meta.get("name_zh") or tool.name,
                "description_zh": meta.get("description_zh")
                or (tool.description[:120] if tool.description else ""),
                "category": meta.get("category") or "其他",
                "requires_confirmation": bool(tool.requires_confirmation),
            }
        )
    return items


def known_tool_names() -> set[str]:
    """内置工具名集合"""
    return {t.name for t in get_tool_definitions()}
