"""图表指标设置意图识别 —— 确定性路由，不依赖模型自觉选 tool

匹配到「打开 RSI / 关 MACD / 布林改虚线」等时，
直接构造 update_indicator_settings 参数，由 gateway 先执行再让模型确认。
"""

from __future__ import annotations

import re
from typing import Any


def _has_any(text: str, keywords: list[str]) -> bool:
    """文本是否包含任一关键词。"""
    return any(key in text for key in keywords)


def detect_indicator_settings_intent(user_text: str) -> dict[str, Any] | None:
    """从用户自然语言中识别图表指标设置意图。

    返回 update_indicator_settings 的 arguments；无法识别返回 None。
    注意：仅处理「改图表显示」类意图，不含「分析某合约 RSI 数值」。
    """
    if not user_text or not isinstance(user_text, str):
        return None

    text = user_text.strip().lower()
    # 去掉常见口语
    text = text.replace("一下", "").replace("帮我", "").replace("请", "")
    text = text.replace("给我", "").replace("把", "")

    # 明确的行情分析用语 → 不走设置路由（避免误伤）
    analysis_markers = [
        "分析",
        "主力",
        "合约",
        "价格",
        "多少钱",
        "涨跌",
        "持仓",
        "新闻",
        "算一下",
        "计算",
        "最新",
        "日线",
        "分钟",
        "rb",
        "au",
        "if",
        "i2",
        "sc",
        "ag",
        "cu",
        "查",
    ]
    # 若同时含设置动词 + 分析词，仍优先设置（如「分析前先打开RSI」）
    settings_verbs = [
        "打开",
        "开启",
        "启用",
        "显示",
        "关闭",
        "关掉",
        "隐藏",
        "禁用",
        "设置",
        "配置",
        "改成",
        "改为",
        "修改",
        "调整",
        "切换",
        "换成",
        "恢复默认",
        "重置",
        "默认指标",
    ]
    has_settings_verb = _has_any(text, settings_verbs)
    has_analysis = _has_any(text, analysis_markers)

    # 纯分析、无设置动词 → 不拦截
    if has_analysis and not has_settings_verb:
        # 特例：仅说「RSI」「布林」且无合约，也可能是想开指标
        pure_toggle = _has_any(
            text,
            ["rsi", "macd", "布林", "boll", "均线", "ma"],
        ) and not re.search(r"[a-z]{1,2}\d{3,4}", text)
        if not pure_toggle:
            return None

    # 恢复默认
    if _has_any(text, ["恢复默认", "重置指标", "默认指标", "恢复指标默认"]):
        return {"reset": True}

    patch: dict[str, Any] = {}
    # 开/关判定
    turn_on = _has_any(
        text,
        ["打开", "开启", "启用", "显示", "加上", "要", "用", "加个", "开一下", "开下"],
    )
    turn_off = _has_any(text, ["关闭", "关掉", "隐藏", "禁用", "去掉", "取消", "不要"])

    # RSI
    if _has_any(text, ["rsi", "相对强弱"]):
        rsi: dict[str, Any] = {}
        if turn_off and not turn_on:
            rsi["enabled"] = False
        else:
            # 默认打开；或仅说 RSI 也打开
            rsi["enabled"] = True
        period_match = re.search(r"rsi\s*[（(]?\s*(\d{1,3})", text)
        if not period_match:
            period_match = re.search(r"周期\s*(\d{1,3})", text)
        if period_match:
            period = int(period_match.group(1))
            if 2 <= period <= 200:
                rsi["period"] = period
        ob = re.search(r"超买\s*(\d{1,3})", text)
        if ob:
            rsi["overbought"] = int(ob.group(1))
        os_m = re.search(r"超卖\s*(\d{1,3})", text)
        if os_m:
            rsi["oversold"] = int(os_m.group(1))
        patch["rsi"] = rsi

    # MACD
    if _has_any(text, ["macd", "指数平滑异同"]):
        macd: dict[str, Any] = {}
        if turn_off and not turn_on:
            macd["enabled"] = False
        else:
            macd["enabled"] = True
        patch["macd"] = macd

    # 布林
    if _has_any(text, ["布林", "boll", "bollinger"]):
        boll: dict[str, Any] = {}
        if turn_off and not turn_on and "实线" not in text and "虚线" not in text:
            boll["enabled"] = False
        else:
            boll["enabled"] = True
        if "实线" in text:
            # 未特指则三轨都实线；特指上/中/下轨则只改该轨
            if "上轨" in text:
                boll["upperStyle"] = "solid"
            elif "中轨" in text:
                boll["middleStyle"] = "solid"
            elif "下轨" in text:
                boll["lowerStyle"] = "solid"
            else:
                boll["upperStyle"] = "solid"
                boll["middleStyle"] = "solid"
                boll["lowerStyle"] = "solid"
        if "虚线" in text:
            if "上轨" in text:
                boll["upperStyle"] = "dashed"
            elif "中轨" in text:
                boll["middleStyle"] = "dashed"
            elif "下轨" in text:
                boll["lowerStyle"] = "dashed"
            else:
                boll["upperStyle"] = "dashed"
                boll["middleStyle"] = "dashed"
                boll["lowerStyle"] = "dashed"
        period_match = re.search(r"(?:周期|period)\s*(\d{1,3})", text)
        if period_match:
            period = int(period_match.group(1))
            if 2 <= period <= 200:
                boll["period"] = period
        std_match = re.search(r"(?:标准差|σ|std)\s*([0-9.]+)", text)
        if std_match:
            try:
                std_val = float(std_match.group(1))
                if 0.5 <= std_val <= 5:
                    boll["std"] = std_val
            except ValueError:
                pass
        patch["boll"] = boll

    # 均线（简单：打开默认三条 / 关闭清空）
    if _has_any(text, ["均线", "ma线"]) or re.search(r"\bma\b", text):
        if turn_off and not turn_on:
            patch["maLines"] = []
        elif turn_on or "均线" in text:
            # 解析「5/10/20」或「5、10、20」
            periods = re.findall(r"(\d{1,3})\s*[/、,，]\s*(\d{1,3})(?:\s*[/、,，]\s*(\d{1,3}))?", text)
            colors = ["#3b82f6", "#f59e0b", "#a855f7", "#ec4899", "#14b8a6"]
            if periods:
                p0, p1, p2 = periods[0]
                nums = [int(p0), int(p1)] + ([int(p2)] if p2 else [])
                lines = []
                for i, num in enumerate(nums[:5]):
                    if 2 <= num <= 500:
                        lines.append({"period": num, "color": colors[i % len(colors)]})
                if lines:
                    patch["maLines"] = lines
            elif turn_on or "默认" in text:
                patch["maLines"] = [
                    {"period": 5, "color": "#3b82f6"},
                    {"period": 10, "color": "#f59e0b"},
                    {"period": 20, "color": "#a855f7"},
                ]

    if not patch:
        # 仅「打开指标」等过宽语句不处理
        return None

    # 若没有任何设置语义且只是提到指标名，仍允许打开
    if not has_settings_verb and not turn_on and not turn_off:
        # 用户只说了「RSI」且 patch 只有 enabled true — 允许
        only_enable = all(
            isinstance(v, dict) and set(v.keys()) <= {"enabled"} or True
            for v in patch.values()
        )
        if not only_enable and not any(
            isinstance(v, dict) and v.get("enabled") for v in patch.values()
        ):
            return None

    return patch


def last_user_text(messages: list[dict[str, Any]]) -> str:
    """提取最后一条用户文本。"""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            return " ".join(parts)
        return str(content or "")
    return ""


async def force_indicator_settings(
    messages: list[dict[str, Any]],
    indicator_config: dict[str, Any] | None,
    api_type: str,
    max_tool_rounds: int,
) -> tuple[list[Any], list[dict[str, Any]], dict[str, Any] | None] | None:
    """若用户意图是改图表指标，强制执行 update_indicator_settings。

    返回 (StreamEvent 列表, 更新后的 messages, 更新后的 config)；无需强制时 None。
    """
    import json

    from app.services.ai.indicator_settings import tool_update_indicator_settings
    from app.services.ai.providers.base import StreamEvent
    from app.services.ai.tools.loop import (
        build_assistant_tool_call_message,
        build_tool_result_message,
    )

    user_text = last_user_text(messages)
    args = detect_indicator_settings_intent(user_text)
    if args is None:
        return None

    call_id = "forced_indicator_settings"
    result = tool_update_indicator_settings(args, indicator_config)
    result_str = json.dumps(result, ensure_ascii=False, default=str)
    new_config = indicator_config
    if result.get("success") and isinstance(result.get("apply_config"), dict):
        new_config = result["apply_config"]

    tool_call = {
        "tool_name": "update_indicator_settings",
        "tool_args": json.dumps(args, ensure_ascii=False),
        "tool_call_id": call_id,
        "tool_args_parsed": args,
    }
    events: list[StreamEvent] = [
        StreamEvent(
            type="react_round",
            content="1",
            tool_call_id=str(max_tool_rounds),
        ),
        StreamEvent(
            type="tool_call",
            tool_name="update_indicator_settings",
            tool_args=tool_call["tool_args"],
            tool_call_id=call_id,
        ),
        StreamEvent(
            type="tool_result",
            tool_name="update_indicator_settings",
            content=result_str,
            tool_call_id=call_id,
        ),
    ]

    next_messages = list(messages)
    next_messages.append(build_assistant_tool_call_message([tool_call], api_type))
    next_messages.append(
        build_tool_result_message(
            call_id,
            "update_indicator_settings",
            result_str,
            api_type,
        )
    )
    next_messages.append(
        {
            "role": "user",
            "content": (
                "系统已按请求执行了图表指标设置（update_indicator_settings）。"
                "请用一两句话确认已修改的内容（如已开启 RSI），"
                "不要询问合约代码，不要再调用 get_quote/get_kline/calculate_indicator。"
            ),
        }
    )
    return events, next_messages, new_config
