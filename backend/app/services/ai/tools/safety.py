READ_RETRY_TOOLS = frozenset({
    "get_quote", "get_kline", "get_open_interest_series", "get_master_contract",
    "get_market_snapshot", "get_news", "get_big_orders", "get_orderbook",
    "calculate_indicator", "get_indicator_settings",
})

MODE_UNAWARE_TOOLS = frozenset({
    "get_open_interest_series", "get_master_contract",
    "get_market_snapshot", "get_big_orders", "get_orderbook",
    "research_products",
})

CONFIRMATION_UNAVAILABLE = {
    "success": False,
    "error_code": "server_confirmation_required",
    "error": "聊天交易及代码执行暂未开放：服务器逐笔确认和幂等链路尚未验收。操作未执行；模拟下单请使用交易页面，不要通过聊天重试确认。",
    "manual_route": "/trading",
}


def requires_server_confirmation(name: str) -> bool:
    from app.services.ai.tools.definitions import get_tool_map

    definition = get_tool_map().get(name)
    return definition is not None and definition.requires_confirmation
