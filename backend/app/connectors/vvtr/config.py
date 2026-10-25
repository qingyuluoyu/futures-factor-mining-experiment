"""VVTR 运行时配置 —— DB system_settings（apiKey 加密存储）+ Redis 状态镜像

配置读取有三条路径，按调用方环境选择：
1. async（bridge / admin API）：get_vvtr_runtime_config(session_factory)
   直接查 system_settings，进程内缓存 10s，管理端改密钥后 10s 内生效；
2. sync（K线 fetcher，跑在 to_thread）：get_vvtr_config_sync()
   env VVTR_API_KEY 优先，否则 asyncio.run 直连 PG 读取，缓存 60s；
3. 热路径（market_channel 每秒循环）：read_vvtr_state_from_redis()
   只读非秘密开关镜像 market:vvtr:state（bridge / admin 写入，TTL 300s），
   避免每秒打 DB；镜像缺失视为关闭，自然回落 sina。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("qihuo.vvtr.config")

# 行情源标识（Redis source 键命名空间 market:live:source:vvtr:quote:*）
VVTR_SOURCE = "vvtr"

DEFAULT_BASE_URL = "https://rest.vvtr.com/v1"
DEFAULT_WS_URL = "wss://rest.vvtr.com/v2/connect"

# type=14 订阅全部国内期货交易所（VVTR 支持直接提交交易所代码）
VVTR_EXCHANGES = ("SHFE", "INE", "DCE", "CZCE", "CFFEX", "GFEX")

# 非秘密状态镜像键（enabled/quotes/kline 开关，供 1s 热路径读取）
VVTR_STATE_KEY = "market:vvtr:state"
_STATE_TTL = 300

# 内部交易所中文名（contracts.py）→ VVTR 交易所代码
_CN_EXCHANGE_TO_VVTR: dict[str, str] = {
    "上期所": "SHFE",
    "上海期货交易所": "SHFE",
    "能源中心": "INE",
    "上海国际能源": "INE",
    "大商所": "DCE",
    "大连商品交易所": "DCE",
    "郑商所": "CZCE",
    "郑州商品交易所": "CZCE",
    "中金所": "CFFEX",
    "中国金融期货交易所": "CFFEX",
    "广期所": "GFEX",
    "广州期货交易所": "GFEX",
}

# VVTR 交易所代码 → 内部交易所中文名（quote.exchange 展示用）
_VVTR_EXCHANGE_TO_CN: dict[str, str] = {
    "SHFE": "上期所",
    "INE": "能源中心",
    "DCE": "大商所",
    "CZCE": "郑商所",
    "CFFEX": "中金所",
    "GFEX": "广期所",
}

# 主力/次主力/指数后缀（VVTR 特有合成合约，内部无对应消费方，直接丢弃）
_MASTER_SUFFIXES = ("9999", "9998", "8888")


def is_master_symbol(symbol: str) -> bool:
    """rb9999 / rb9998 / rb8888 形式的合成合约"""
    s = str(symbol or "").strip().lower()
    for suffix in _MASTER_SUFFIXES:
        if s.endswith(suffix) and len(s) > len(suffix):
            head = s[: -len(suffix)]
            if head.isalpha():
                return True
    return False


def to_vvtr_symbol(symbol: str) -> str:
    """内部合约 → VVTR 合约（小写；如 rb2610 → rb2610）"""
    return str(symbol or "").strip().lower()


def vvtr_exchange_to_cn(exchange_code: str | None) -> str:
    return _VVTR_EXCHANGE_TO_CN.get(str(exchange_code or "").strip().upper(), "")


def match_valid_vvtr_symbols(masters: list[str], valid: list[str]) -> list[str]:
    """主力列表 ∩ VVTR 有效合约清单（统一大写，保序）

    VVTR /v1/symbols 返回大写合约（如 RB2610）；订阅中混入任一
    无效/下市合约会导致整个订阅零推送，必须先对清单过滤。
    """
    valid_set = {str(v or "").strip().upper() for v in valid if str(v or "").strip()}
    out: list[str] = []
    seen: set[str] = set()
    for m in masters or []:
        u = str(m or "").strip().upper()
        if not u or u in seen or u not in valid_set:
            continue
        seen.add(u)
        out.append(u)
    return out


def cap_symbol_list(symbols: list[str], cap: int = 100) -> list[str]:
    """订阅合约清洗：去空/去重/小写/剔除 9999 等合成主力，截断到上限

    官方规则：单连接最大订阅 100 个合约；按用户策略只订阅各品种
    真实主力合约，VVTR 的 9999/9998/8888 合成主力暂不订阅。
    """
    out: list[str] = []
    seen: set[str] = set()
    for s in symbols or []:
        v = str(s or "").strip().lower()
        if not v or v in seen or is_master_symbol(v):
            continue
        seen.add(v)
        out.append(v)
        if len(out) >= cap:
            break
    return out


@dataclass
class VvtrRuntimeConfig:
    """VVTR 运行时配置（含解密后的 apiKey，仅进程内传递，禁止落日志）"""

    enabled: bool = False
    quotes_enabled: bool = True
    ws_enabled: bool = True
    kline_enabled: bool = False
    api_key: str = ""
    mobile: str = ""
    base_url: str = DEFAULT_BASE_URL
    ws_url: str = DEFAULT_WS_URL

    @property
    def ws_active(self) -> bool:
        """实时行情链路可用：总开关 + 实时开关 + 已配置密钥"""
        return bool(self.enabled and self.quotes_enabled and self.api_key)


def _decrypt_key(raw: Any) -> str:
    """解密存储的 apiKey；空/损坏返回空串（不抛错，避免拖垮调用方）"""
    raw = str(raw or "").strip()
    if not raw:
        return ""
    try:
        from app.services.crypto import decrypt

        return decrypt(raw)
    except Exception:
        logger.warning("VVTR apiKey 解密失败（AES_ENCRYPTION_KEY 变更或数据损坏）")
        return ""


def _row_to_config(row: Any) -> VvtrRuntimeConfig:
    # 兼容 SQLAlchemy RowMapping（dict 子类）与 asyncpg Record（不支持属性访问）
    data: dict[str, Any] = dict(row) if not isinstance(row, dict) else row
    return VvtrRuntimeConfig(
        enabled=bool(data.get("vvtr_enabled", False)),
        quotes_enabled=bool(data.get("vvtr_quotes_enabled", True)),
        ws_enabled=bool(data.get("vvtr_ws_enabled", True)),
        kline_enabled=bool(data.get("vvtr_kline_enabled", False)),
        api_key=_decrypt_key(data.get("vvtr_api_key", "")),
        mobile=str(data.get("vvtr_mobile", "") or "").strip(),
        base_url=(
            str(data.get("vvtr_base_url", "") or "").strip()
            or DEFAULT_BASE_URL
        ),
        ws_url=str(data.get("vvtr_ws_url", "") or "").strip() or DEFAULT_WS_URL,
    )


# ---------- async 路径（bridge / admin，10s 进程内缓存） ----------

_async_cache: dict[str, Any] = {"ts": 0.0, "cfg": None}
_ASYNC_CACHE_TTL = 10.0


async def get_vvtr_runtime_config(
    session_factory: Any,
    *,
    force: bool = False,
) -> VvtrRuntimeConfig:
    """读取 VVTR 运行时配置（缓存 10s；force=True 立即刷新）"""
    now = time.monotonic()
    cached = _async_cache.get("cfg")
    if not force and cached is not None and now - float(_async_cache.get("ts") or 0) < _ASYNC_CACHE_TTL:
        return cached  # type: ignore[return-value]
    cfg = VvtrRuntimeConfig()
    if session_factory is not None:
        try:
            async with session_factory() as session:
                from sqlalchemy import text as _text

                result = await session.execute(
                    _text(
                        "SELECT vvtr_enabled, vvtr_quotes_enabled, vvtr_ws_enabled, vvtr_kline_enabled, "
                        "vvtr_api_key, vvtr_mobile, vvtr_base_url, vvtr_ws_url "
                        "FROM system_settings WHERE id = 1"
                    )
                )
                row = result.mappings().first()
                if row is not None:
                    cfg = _row_to_config(row)
        except Exception:
            logger.warning("读取 VVTR 配置失败（表/列未就绪？）", exc_info=True)
    _async_cache["ts"] = now
    _async_cache["cfg"] = cfg
    return cfg


def invalidate_async_cache() -> None:
    """管理端保存配置后调用，让下一次读取立即生效"""
    _async_cache["ts"] = 0.0


# ---------- sync 路径（K线 fetcher，跑在 to_thread） ----------

_sync_cache: dict[str, Any] = {"ts": 0.0, "cfg": None}
_SYNC_CACHE_TTL = 60.0


def get_vvtr_config_sync() -> VvtrRuntimeConfig:
    """同步读取 VVTR 配置（供 to_thread 中的 K线 fetcher）

    优先级：env VVTR_API_KEY（开发调试）→ 进程缓存 → asyncio.run 直连 PG。
    只能在无事件循环的线程中调用（与 kline_provider._fetch_simnow 同模式）。
    """
    env_key = os.getenv("VVTR_API_KEY", "").strip()
    if env_key:
        return VvtrRuntimeConfig(
            enabled=True,
            quotes_enabled=True,
            kline_enabled=True,
            api_key=env_key,
            base_url=os.getenv("VVTR_BASE_URL", "").strip() or DEFAULT_BASE_URL,
            ws_url=os.getenv("VVTR_WS_URL", "").strip() or DEFAULT_WS_URL,
        )
    now = time.monotonic()
    cached = _sync_cache.get("cfg")
    if cached is not None and now - float(_sync_cache.get("ts") or 0) < _SYNC_CACHE_TTL:
        return cached  # type: ignore[return-value]
    cfg = VvtrRuntimeConfig()
    try:
        cfg = asyncio.run(_load_config_from_pg())
    except RuntimeError:
        # 在事件循环内调用（不应发生）；退回缓存或默认
        logger.debug("VVTR sync 配置读取跳过：处于事件循环内")
    except Exception:
        logger.debug("VVTR sync 配置读取失败", exc_info=True)
    _sync_cache["ts"] = now
    _sync_cache["cfg"] = cfg
    return cfg


async def _load_config_from_pg() -> VvtrRuntimeConfig:
    import asyncpg

    from app.core.config import load_settings

    s = load_settings()
    conn = await asyncpg.connect(
        host=s.postgres_host,
        port=s.postgres_port,
        user=s.postgres_user,
        password=s.postgres_password,
        database=s.postgres_db,
    )
    try:
        row = await conn.fetchrow(
            "SELECT vvtr_enabled, vvtr_quotes_enabled, vvtr_ws_enabled, vvtr_kline_enabled, "
            "vvtr_api_key, vvtr_mobile, vvtr_base_url, vvtr_ws_url "
            "FROM system_settings WHERE id = 1"
        )
        if row is None:
            return VvtrRuntimeConfig()
        return _row_to_config(row)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


# ---------- Redis 状态镜像（1s 热路径读，不含秘密） ----------


async def write_vvtr_state_to_redis(
    redis_conn: aioredis.Redis,
    *,
    enabled: bool,
    quotes_enabled: bool,
    kline_enabled: bool,
    ws_enabled: bool = True,
) -> None:
    """写入非秘密开关镜像（admin 保存 / bridge 周期刷新）"""
    payload = {
        "enabled": bool(enabled),
        "quotes_enabled": bool(quotes_enabled),
        "kline_enabled": bool(kline_enabled),
        "ws_enabled": bool(ws_enabled),
        "ts": time.time(),
    }
    await redis_conn.set(VVTR_STATE_KEY, json.dumps(payload), ex=_STATE_TTL)


async def read_vvtr_state_from_redis(
    redis_conn: aioredis.Redis | None,
) -> dict[str, bool]:
    """读取开关镜像；缺失/异常一律视为关闭（安全回落 sina）"""
    out = {
        "enabled": False,
        "quotes_enabled": False,
        "kline_enabled": False,
        "ws_enabled": True,
    }
    if redis_conn is None:
        return out
    try:
        raw = await redis_conn.get(VVTR_STATE_KEY)
        if not raw:
            return out
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        data = json.loads(raw)
        if isinstance(data, dict):
            out["enabled"] = bool(data.get("enabled"))
            out["quotes_enabled"] = bool(data.get("quotes_enabled"))
            out["kline_enabled"] = bool(data.get("kline_enabled"))
            out["ws_enabled"] = bool(data.get("ws_enabled", True))
    except Exception:
        logger.debug("读取 VVTR 状态镜像失败", exc_info=True)
    return out
