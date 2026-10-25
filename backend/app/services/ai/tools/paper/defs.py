"""模拟交易 ToolDef 定义"""

from __future__ import annotations

from app.services.ai.providers.base import ToolDef


def get_paper_trade_tool_definitions() -> list[ToolDef]:
    """模拟交易相关 ToolDef（写操作需用户确认）"""
    confirmed_prop = {
        "type": "boolean",
        "description": (
            "用户已明确确认后传 true 才会真正下单/撤单；"
            "首次调用不要传或传 false"
        ),
    }
    return [
        ToolDef(
            name="paper_open_long",
            description=(
                "【模拟交易】买入开多（做多）。"
                "order_type=market 立即按参考价成交；limit 限价挂单。"
                "首次调用将请求用户确认；用户同意后再次调用并传 confirmed=true。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "合约代码，如 rb2610"},
                    "quantity": {"type": "integer", "description": "手数，1~10000"},
                    "order_type": {
                        "type": "string",
                        "enum": ["market", "limit"],
                        "description": "市价/限价，默认 market",
                    },
                    "price": {
                        "type": "number",
                        "description": "价格；市价用最新参考价，限价为委托价",
                    },
                    "symbol_name": {"type": "string", "description": "合约名称可选"},
                    "confirmed": confirmed_prop,
                },
                "required": ["symbol", "quantity", "price"],
            },
            requires_confirmation=True,
        ),
        ToolDef(
            name="paper_open_short",
            description=(
                "【模拟交易】卖出开空（做空）。"
                "首次调用请求确认；用户同意后再次调用并传 confirmed=true。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "合约代码"},
                    "quantity": {"type": "integer", "description": "手数"},
                    "order_type": {
                        "type": "string",
                        "enum": ["market", "limit"],
                        "description": "默认 market",
                    },
                    "price": {"type": "number", "description": "委托/参考价"},
                    "symbol_name": {"type": "string", "description": "合约名称可选"},
                    "confirmed": confirmed_prop,
                },
                "required": ["symbol", "quantity", "price"],
            },
            requires_confirmation=True,
        ),
        ToolDef(
            name="paper_close_position",
            description=(
                "【模拟交易】平仓。"
                "position_direction=long 平多(卖平)；short 平空(买平)。"
                "用户确认后传 confirmed=true 执行。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "合约代码"},
                    "position_direction": {
                        "type": "string",
                        "enum": ["long", "short"],
                        "description": "要平的持仓方向",
                    },
                    "quantity": {"type": "integer", "description": "平仓手数"},
                    "order_type": {
                        "type": "string",
                        "enum": ["market", "limit"],
                        "description": "默认 market",
                    },
                    "price": {"type": "number", "description": "价格"},
                    "symbol_name": {"type": "string", "description": "可选"},
                    "confirmed": confirmed_prop,
                },
                "required": [
                    "symbol",
                    "position_direction",
                    "quantity",
                    "price",
                ],
            },
            requires_confirmation=True,
        ),
        ToolDef(
            name="paper_place_limit",
            description=(
                "【模拟交易】限价挂单。"
                "开多 buy+open；开空 sell+open；平多 sell+close；平空 buy+close。"
                "用户确认后 confirmed=true。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "direction": {
                        "type": "string",
                        "enum": ["buy", "sell"],
                    },
                    "offset": {
                        "type": "string",
                        "enum": ["open", "close"],
                    },
                    "price": {"type": "number", "description": "限价"},
                    "quantity": {"type": "integer"},
                    "symbol_name": {"type": "string"},
                    "confirmed": confirmed_prop,
                },
                "required": [
                    "symbol",
                    "direction",
                    "offset",
                    "price",
                    "quantity",
                ],
            },
            requires_confirmation=True,
        ),
        ToolDef(
            name="paper_cancel_order",
            description=(
                "【模拟交易】撤销 pending 挂单。"
                "用户确认后 confirmed=true。order_id 为委托 UUID。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "委托 ID",
                    },
                    "confirmed": confirmed_prop,
                },
                "required": ["order_id"],
            },
            requires_confirmation=True,
        ),
        ToolDef(
            name="paper_list_positions",
            description="【模拟交易】查询当前持仓列表（方向、均价、手数、保证金等）。",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        ToolDef(
            name="paper_list_orders",
            description=(
                "【模拟交易】查询委托。status 可选 pending/filled/cancelled；"
                "limit 默认 20。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pending", "filled", "cancelled", "rejected"],
                        "description": "过滤状态，不传则全部",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "条数 1~100，默认 20",
                    },
                },
                "required": [],
            },
        ),
        ToolDef(
            name="paper_get_account",
            description=(
                "【模拟交易】查询账户：余额、可用保证金、冻结、持仓保证金、盈亏等。"
            ),
            parameters={"type": "object", "properties": {}, "required": []},
        ),
    ]
