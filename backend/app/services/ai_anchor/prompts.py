"""AI 看盘主播 Prompt —— 技术看盘专家人设与上下文组装"""

from __future__ import annotations

import json
from typing import Any

from app.services.factor_lab.llm.llm_http import extract_json_object

# 输出 JSON 中数值价位字段
_PRICE_FIELDS = ("entry", "take_profit", "stop_loss")
# 播报文案长度上限（字符）。多周期播报常达 600-800 字，400 会拦腰截断
_MAX_COMMENTARY = 2000
# 关键压力/支撑位条数上限
_MAX_KEY_LEVELS = 4

# 方向偏好 → 约束文案
_DIRECTION_CONSTRAINTS = {
    "any": "多空双向观察：涨跌机会均可提示。",
    "long": "用户设定只看做多方向：你只寻找做多机会，direction 只能是 long 或 neutral；"
    "action=trade 时方向必须是 long，禁止给出做空建议。",
    "short": "用户设定只看做空方向：你只寻找做空机会，direction 只能是 short 或 neutral；"
    "action=trade 时方向必须是 short，禁止给出做多建议。",
}

# 操盘策略 → 风格约束文案（用户可随时切换，下一轮播报生效）
_STRATEGY_CONSTRAINTS = {
    "aggressive": (
        "操盘策略【激进】：反应果断、快进快出。信号初现（不必等多重确认）即可提示做单；"
        "止损收紧到近期波动近端，优先抓启动点；持仓时更倾向提示加仓博收益；"
        "文案风格干脆利落，敢于下结论。"
    ),
    "balanced": (
        "操盘策略【稳健】：信号与结构确认后再提示做单（至少两重依据）；"
        "止损止盈给出常规合理空间，重视盈亏比；持仓时按趋势持有为主，提示逢低/逢高优化仓位；"
        "文案风格专业平衡，观点明确。"
    ),
    "conservative": (
        "操盘策略【保守】：只在多信号共振、盈亏比明显有利时才提示做单，多数时候建议观望；"
        "止损放到结构关键位之外（宁可错过不可做错），首次进场仓位从轻；"
        "持仓时只要结构未破坏就持有，但风险提示必须充分；"
        "文案风格谨慎，反复强调风险控制。"
    ),
}


# 交易风格（持仓周期维度，与操盘策略正交）→ 约束文案
_HORIZON_CONSTRAINTS = {
    "short": (
        "交易风格【短线】：以分钟级分时节奏为主，快进快出，持仓一般数分钟到数小时；"
        "重点参考较短分时时段的结构与指标，进场用近端关键位，止盈止损空间给窄一些；"
        "点位与时机优先。"
    ),
    "mid": (
        "交易风格【中线】：以波段思路为主，持仓一般数小时到数天；"
        "重点参考中等分时时段的趋势结构，等回调企稳再提示进场，止盈止损给足波动空间；"
        "趋势与盈亏比优先。"
    ),
    "long": (
        "交易风格【长线】：以趋势与大结构为主，持仓一般数天到数周；"
        "重点参考较大分时时段的方向与关键结构位，忽略分钟级噪音，"
        "进出场点位可容忍更大误差；方向判断与仓位管理优先。"
    ),
}


def build_system_prompt(
    direction_mode: str,
    has_position: bool = False,
    strategy: str = "balanced",
    horizon: str = "short",
    timeframe_count: int = 1,
) -> str:
    """主播人设：期货技术看盘专家，输出严格 JSON 结论（含方向偏好/操盘策略/交易风格约束）。"""
    constraint = _DIRECTION_CONSTRAINTS.get(direction_mode, _DIRECTION_CONSTRAINTS["any"])
    strategy_text = _STRATEGY_CONSTRAINTS.get(strategy, _STRATEGY_CONSTRAINTS["balanced"])
    horizon_text = _HORIZON_CONSTRAINTS.get(horizon, _HORIZON_CONSTRAINTS["short"])
    base = (
        "你是一位资深的期货技术看盘专家主播，拥有二十年K线技术与指标分析经验。"
        "你的任务是按照用户设定的品种、分时K线段、根数与技术线组合，"
        "对最新行情进行连贯的技术面解读，并给出可执行的交易结论。\n\n"
    )
    if timeframe_count > 1:
        base += (
            f"用户本次选择了 {timeframe_count} 个分时K线时段，你必须综合多周期共振分析："
            "较大周期判断方向与关键结构，较小周期寻找进场时机；"
            "播报中明确说明各周期是否共振一致，优先提示多周期共振的机会。\n\n"
        )
    base += (
        "输出要求（严格遵守）：\n"
        "1. 只输出单个 JSON 对象，禁止任何 JSON 之外的文字。\n"
        "2. 字段固定为：direction、entry、take_profit、stop_loss、action、"
        "commentary、key_levels"
    )
    if has_position:
        base += "、position_advice"
    base += (
        "。\n"
        "3. direction 只能是 long（做多）/ short（做空）/ neutral（中性无方向）。"
        f"方向约束：{constraint}\n"
        f"{strategy_text}\n"
        f"{horizon_text}\n"
        "4. entry=建议进场位置，take_profit=建议止盈位置，stop_loss=建议止损位置；"
        "均为价格数字；若 action 为 wait（观望），可给参考价位或 null，"
        "但给出方向倾向时应尽量给出关键参考价位。\n"
        "5. action 只能是 trade（做单）或 wait（观望）。信号不明朗、风险收益比不足时果断观望；"
        "具体的做单/观望倾向必须符合上述操盘策略。\n"
        "6. commentary 是你的播报文案，以技术看盘专家口吻用中文播报，"
        "解读多空结构、指标依据与关键价位，不超过300字；"
        "若与此前播报观点发生变化，必须在文案中明确说明观点转变及原因。\n"
    )
    if has_position:
        base += (
            "用户已持仓：commentary 必须先汇报持仓状态（开仓价、当前浮动盈亏点数），"
            "再结合行情给出针对该持仓的操作建议（持有/加仓/减仓/止盈/止损），"
            "提示风险时必须给出明确价位。\n"
            "position_advice 为针对用户持仓的操作建议对象，形如 "
            '{"action": "hold"（持有）/"add"（加仓）/"reduce"（减仓）/"close"（离场）, '
            '"note": "一句话理由"}。\n'
        )
    base += (
        "7. 所有价位必须结合当前价格与合约最小变动价位给出合理数值，禁止明显偏离行情。\n"
        "8. key_levels 为关键价位数组（0-4 条），每条形如 "
        '{"price": 数字, "type": "resistance"（压力位）或 "support"（支撑位）, '
        '"label": "简短说明如 前高压力"}；'
        "基于K线结构（前高/前低/密集成交区/整数关口）给出，price 必须在近期价格范围内。"
    )
    return base


def _format_bars(bars: list[dict[str, Any]]) -> str:
    """K 线压缩为 CSV 行（time,open,high,low,close,volume）。"""
    lines: list[str] = []
    for bar in bars:
        lines.append(
            "{time},{open},{high},{low},{close},{volume}".format(
                time=str(bar.get("time") or ""),
                open=bar.get("open"),
                high=bar.get("high"),
                low=bar.get("low"),
                close=bar.get("close"),
                volume=bar.get("volume"),
            )
        )
    return "\n".join(lines)


def _format_indicators(summaries: list[dict[str, Any]]) -> str:
    """技术线摘要压缩为 JSON 行。"""
    return json.dumps(summaries, ensure_ascii=False, default=str)


def _format_history(recent: list[dict[str, Any]]) -> str:
    """最近播报记录（保持连贯分析的上下文）。"""
    if not recent:
        return "（首次播报，暂无历史）"
    rows: list[str] = []
    for item in recent:
        rows.append(
            "- {time} 方向={direction} 动作={action} "
            "进场={entry} 止盈={take_profit} 止损={stop_loss} 摘要={summary}".format(
                time=item.get("time"),
                direction=item.get("direction"),
                action=item.get("action"),
                entry=item.get("entry"),
                take_profit=item.get("take_profit"),
                stop_loss=item.get("stop_loss"),
                summary=str(item.get("commentary") or "")[:200],
            )
        )
    return "\n".join(rows)


def _format_position(position_info: dict[str, Any] | None) -> str:
    """用户持仓段（持仓互动播报）；空持仓返回空串。"""
    if not position_info:
        return ""
    direction = "做多" if position_info.get("direction") == "long" else "做空"
    pnl = position_info.get("pnl_points")
    pnl_text = f"，当前浮动盈亏 {pnl:+g} 点" if isinstance(pnl, (int, float)) else ""
    return (
        "\n【用户当前持仓（本轮播报必须优先汇报持仓状态并给持仓操作建议）】\n"
        "- 合约 {symbol} {direction} {quantity} 手，开仓价 {price}{pnl}\n".format(
            symbol=position_info.get("symbol"),
            direction=direction,
            quantity=position_info.get("quantity"),
            price=position_info.get("price"),
            pnl=pnl_text,
        )
    )


def build_user_prompt(
    symbol: str,
    segments: list[dict[str, Any]],
    bar_count: int,
    recent_broadcasts: list[dict[str, Any]],
    position_info: dict[str, Any] | None = None,
) -> str:
    """组装单轮分析的用户消息：多时段K线窗口 + 技术线摘要 + 历史播报 +（可选）用户持仓。

    segments 每项形如 {"timeframe": "15m", "bars": [...], "indicators": [...]}；
    第一段为主时段（持仓浮盈/播报周期以它为准）。
    """
    tf_text = "/".join(str(seg.get("timeframe") or "") for seg in segments)
    blocks: list[str] = []
    for idx, seg in enumerate(segments):
        bars = list(seg.get("bars") or [])
        latest = bars[-1] if bars else {}
        title = f"【时段{idx + 1}：{seg.get('timeframe')}"
        if idx == 0:
            title += "（主时段）"
        title += (
            f"，最新K线时间 {latest.get('time')}，最新收盘价 {latest.get('close')}】"
        )
        blocks.append(
            "{title}\n"
            "K线数据（time,open,high,low,close,volume）：\n{bars}\n"
            "技术线计算结果（含最新值/近几拍/信号）：\n{indicators}".format(
                title=title,
                bars=_format_bars(bars),
                indicators=_format_indicators(list(seg.get("indicators") or [])),
            )
        )
    return (
        "品种：{symbol}；分时K线段：{tfs}；分析窗口：每时段最新 {count} 根"
        "（各段最后一根为该时段最新K线）。\n\n"
        "{blocks}\n\n"
        "【你的最近播报记录（保持观点连贯；观点变化需说明原因）】\n{history}\n"
        "{position}"
        "请综合以上各时段数据给出本轮播报 JSON。"
    ).format(
        symbol=symbol,
        tfs=tf_text,
        count=bar_count,
        blocks="\n\n".join(blocks),
        history=_format_history(recent_broadcasts),
        position=_format_position(position_info),
    )


def _as_price(value: Any) -> float | None:
    """价位字段安全转 float，非法值归 None。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _parse_key_levels(raw: Any) -> list[dict[str, Any]]:
    """解析关键压力/支撑位：限 0-4 条，price 必须为有效数字。"""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:_MAX_KEY_LEVELS]:
        if not isinstance(item, dict):
            continue
        price = _as_price(item.get("price"))
        if price is None:
            continue
        level_type = str(item.get("type") or "").lower()
        if level_type not in ("resistance", "support"):
            continue
        out.append(
            {
                "price": price,
                "type": level_type,
                "label": str(item.get("label") or "").strip()[:60],
            }
        )
    return out


# 持仓建议 action 白名单
_POSITION_ACTIONS = ("hold", "add", "reduce", "close")


def _parse_position_advice(raw: Any) -> dict[str, Any] | None:
    """解析持仓操作建议；action 非法或结构不对返回 None。"""
    if not isinstance(raw, dict):
        return None
    action = str(raw.get("action") or "").lower()
    if action not in _POSITION_ACTIONS:
        return None
    return {"action": action, "note": str(raw.get("note") or "").strip()[:160]}


def parse_anchor_output(text: str) -> dict[str, Any]:
    """解析模型输出为播报结论；解析失败返回带错误标记的观望结论。"""
    data = extract_json_object(text)
    if data is None:
        return {
            "direction": "neutral",
            "entry": None,
            "take_profit": None,
            "stop_loss": None,
            "action": "wait",
            "commentary": "模型未返回合法 JSON，本轮播报降级为观望。",
            "key_levels": [],
            "parse_ok": False,
        }
    direction = str(data.get("direction") or "neutral").lower()
    if direction not in ("long", "short", "neutral"):
        direction = "neutral"
    action = str(data.get("action") or "wait").lower()
    if action not in ("trade", "wait"):
        action = "wait"
    commentary = str(data.get("commentary") or "").strip()[:_MAX_COMMENTARY]
    result: dict[str, Any] = {
        "direction": direction,
        "action": action,
        "commentary": commentary,
        "key_levels": _parse_key_levels(data.get("key_levels")),
        "position_advice": _parse_position_advice(data.get("position_advice")),
        "parse_ok": True,
    }
    for field in _PRICE_FIELDS:
        result[field] = _as_price(data.get(field))
    return result
