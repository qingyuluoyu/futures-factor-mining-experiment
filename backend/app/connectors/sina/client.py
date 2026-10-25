"""backend 进程侧的新浪桥 HTTP 客户端

backend 不再直连新浪（出网集中在 qihuo-sina-bridge 容器），
K 线主源 / 单合约 quote 兜底经桥 HTTP 获取。接口形状与
app.data.sina_kline / sina_quote 的同名函数一致，错误返回
空值保持现有降级语义（kline_provider 健康冷却依赖空结果计失败）。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import bridge_base_url

logger = logging.getLogger("qihuo.sina_client")


def _get_json(path: str, params: dict[str, Any], timeout: float) -> Any:
    url = bridge_base_url() + path
    try:
        resp = httpx.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError:
        logger.warning("sina-bridge 请求失败 %s %s", path, params)
        return None
    except (ValueError, TypeError):
        logger.warning("sina-bridge 响应非 JSON %s", path)
        return None


def fetch_minute_kline_via_bridge(
    symbol: str,
    period: str,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """分钟 K 线（含 1m 及聚合周期），失败返回 []"""
    data = _get_json(
        "/kline/minute", {"symbol": symbol, "period": period}, timeout=timeout
    )
    if not isinstance(data, list):
        return []
    return [b for b in data if isinstance(b, dict)]


def fetch_daily_kline_via_bridge(
    symbol: str,
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    """日 K 线，失败返回 []"""
    data = _get_json("/kline/daily", {"symbol": symbol}, timeout=timeout)
    if not isinstance(data, list):
        return []
    return [b for b in data if isinstance(b, dict)]


def fetch_quote_via_bridge(
    symbol: str,
    timeout: float = 15.0,
) -> dict[str, Any] | None:
    """单合约实时 quote（桥按需拉新浪），失败/未找到返回 None"""
    data = _get_json(f"/quote/{symbol}", {}, timeout=timeout)
    return data if isinstance(data, dict) else None


def fetch_quotes_via_bridge(
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """最近一轮成功轮询的缓存快照，失败返回 []"""
    data = _get_json("/quotes", {}, timeout=timeout)
    if not isinstance(data, list):
        return []
    return [q for q in data if isinstance(q, dict)]
