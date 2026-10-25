"""图表指标配置读写 —— 与前端 DEFAULT_INDICATOR_CONFIG 对齐

供 Agent 工具 get/update_indicator_settings 使用。
update 返回 apply_config，由前端落盘到 indicator store。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

DEFAULT_INDICATOR_CONFIG: dict[str, Any] = {
    "maLines": [
        {"period": 5, "color": "#3b82f6"},
        {"period": 10, "color": "#f59e0b"},
        {"period": 20, "color": "#a855f7"},
    ],
    "macd": {
        "enabled": True,
        "fastPeriod": 12,
        "slowPeriod": 26,
        "signalPeriod": 9,
        "difColor": "#3b82f6",
        "deaColor": "#f59e0b",
        "histUpColor": "rgba(239,68,68,0.6)",
        "histDownColor": "rgba(34,197,94,0.6)",
    },
    "rsi": {
        "enabled": False,
        "period": 14,
        "overbought": 70,
        "oversold": 30,
        "lineColor": "#14b8a6",
        "overboughtColor": "rgba(239,68,68,0.7)",
        "oversoldColor": "rgba(34,197,94,0.7)",
    },
    "boll": {
        "enabled": False,
        "period": 20,
        "std": 2,
        "upperColor": "#a855f7",
        "middleColor": "#f59e0b",
        "lowerColor": "#a855f7",
        "upperStyle": "dashed",
        "middleStyle": "solid",
        "lowerStyle": "dashed",
    },
}


def _merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """浅层合并嵌套 dict。"""
    out = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out


def _normalize_config(raw: Any) -> dict[str, Any]:
    """兼容旧配置，缺字段补默认。"""
    if not isinstance(raw, dict):
        return deepcopy(DEFAULT_INDICATOR_CONFIG)
    return _merge_dict(DEFAULT_INDICATOR_CONFIG, raw)


def tool_get_indicator_settings(
    current_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """读取当前图表指标配置。"""
    config = _normalize_config(current_config)
    return {
        "success": True,
        "config": config,
        "note": "此为用户图表指标设置；与 calculate_indicator 的数值计算相互独立",
    }


def tool_update_indicator_settings(
    arguments: dict[str, Any],
    current_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """生成新指标配置；前端须应用 apply_config。"""
    base = _normalize_config(current_config)
    if arguments.get("reset") is True:
        config = deepcopy(DEFAULT_INDICATOR_CONFIG)
        return {
            "success": True,
            "applied": True,
            "action": "reset",
            "config": config,
            "apply_config": config,
            "message": "已恢复默认指标配置，请前端立即应用",
        }

    patch: dict[str, Any] = {}
    raw_config = arguments.get("config")
    if isinstance(raw_config, dict):
        patch.update(raw_config)
    for key in ("maLines", "macd", "rsi", "boll"):
        if key in arguments and arguments[key] is not None:
            patch[key] = arguments[key]

    if not patch:
        return {
            "success": False,
            "error": "未提供任何修改；请传 config / maLines / macd / rsi / boll 或 reset=true",
        }

    if "maLines" in patch:
        lines = patch["maLines"]
        if not isinstance(lines, list):
            return {"success": False, "error": "maLines 必须是数组"}
        if len(lines) > 5:
            return {"success": False, "error": "均线最多 5 条"}
        for line in lines:
            if not isinstance(line, dict) or "period" not in line:
                return {"success": False, "error": "maLines 项需含 period"}
            period = int(line["period"])
            if period < 2 or period > 500:
                return {"success": False, "error": "均线周期需在 2-500"}
            if "color" not in line:
                line["color"] = "#3b82f6"

    for style_key in ("upperStyle", "middleStyle", "lowerStyle"):
        boll_patch = patch.get("boll")
        if isinstance(boll_patch, dict) and style_key in boll_patch:
            if boll_patch[style_key] not in ("solid", "dashed"):
                return {
                    "success": False,
                    "error": f"boll.{style_key} 仅支持 solid/dashed",
                }

    config = _merge_dict(base, patch)
    return {
        "success": True,
        "applied": True,
        "action": "update",
        "config": config,
        "apply_config": config,
        "changed_keys": list(patch.keys()),
        "message": "指标配置已更新，请前端立即应用到图表",
    }
