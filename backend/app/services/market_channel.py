"""行情渠道路由 —— sina / openctp / real_ctp / vvtr / ctp_sim（开源版：默认仅新浪）

管理端 system_settings.market_data_channel：
- sina：新浪源键（qihuo-sina-bridge 容器轮询写 source 键，本进程只读 Redis）
- openctp_724：openctp 7×24（trading.openctp.cn:30011，虚拟盘固定渠道）
- openctp_sim：openctp 仿真（行情宜直连实盘前置，见 .env）
- real_ctp：真实期货公司 CTP（Level-1 一档，无风控）
- vvtr：VVTR WS 深度行情（ws_bridge 写源键）
- ctp_sim：CTP 中转模拟（7×24 任意账号，订阅即推快照 tick；
  仅 Tick/K线实时联调用，非真实数据，配置存 DB 镜像 Redis）
（SimNow 渠道已下线 2026-08-24：服务端行情静默僵死频发，
 历史配置残留值经 normalize_channel 自动回落 sina）

Redis 热路径：
- live: market:live:channel（兼容旧 market:channel）
- virtual: market:virtual:channel（固定 openctp_724，可 CTP_BRIDGE_VIRTUAL_CHANNEL 换源）
- ctp_sim 配置镜像: market:ctp_sim:config（管理端保存后写入，bridge 重启读取）
K 线历史始终新浪（经 sina-bridge HTTP）。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import redis.asyncio as aioredis

from app.core.trading_mode import MODE_LIVE, MODE_VIRTUAL
from app.data.contracts import ALL_CONTRACTS
from app.data.quote_schema import filter_valid_quotes, is_valid_quote
from app.services.market_keys import (
    LEGACY_CHANNEL_KEY,
    channel_key,
    ctp_front_key,
    quote_key,
    quote_key_prefix,
    source_quote_key,
)

logger = logging.getLogger("qihuo.market_channel")

CHANNEL_SINA = "sina"
CHANNEL_OPENCTP_724 = "openctp_724"
CHANNEL_OPENCTP_SIM = "openctp_sim"
# 真实期货公司 CTP 行情（Level-1 一档，交易所 OHLC，无风控）
CHANNEL_REAL_CTP = "real_ctp"
# VVTR（长尾猴量化）WS v2 深度行情：5 档盘口 250~500ms 推送，
# 数据由 scheduler master 内的 vvtr ws_bridge 写 source 键
CHANNEL_VVTR = "vvtr"
# CTP 中转模拟行情（7×24，任意账号可登录；订阅即推快照 tick，
# 仅 Tick/K线实时链路，无历史 K 线；非真实数据，仅用于休市联调）
CHANNEL_CTP_SIM = "ctp_sim"
VALID_CHANNELS: frozenset[str] = frozenset(
    {
        CHANNEL_SINA,
        CHANNEL_OPENCTP_724,
        CHANNEL_OPENCTP_SIM,
        CHANNEL_REAL_CTP,
        CHANNEL_VVTR,
        CHANNEL_CTP_SIM,
    }
)

# CTP 类渠道：实时 quote 来自 bridge 写的 Redis
CTP_CHANNELS: frozenset[str] = frozenset(
    {
        CHANNEL_OPENCTP_724,
        CHANNEL_OPENCTP_SIM,
        CHANNEL_REAL_CTP,
        CHANNEL_CTP_SIM,
    }
)

# 非 7×24 真实数据渠道（跟随交易时间，可选用作备用）
REALTIME_CHANNELS: frozenset[str] = frozenset(
    {
        CHANNEL_SINA,
        CHANNEL_OPENCTP_SIM,
        CHANNEL_REAL_CTP,
        CHANNEL_VVTR,
    }
)

DEFAULT_OPENCTP_MD_FRONT_724 = "tcp://trading.openctp.cn:30011"
DEFAULT_OPENCTP_MD_FRONT_SIM = "tcp://trading.openctp.cn:30011"
# CTP 中转模拟前置（2026-10-11 实测：任意账号可登录，openctp_ctp 官方库）
DEFAULT_CTP_SIM_MD_FRONT = ""  # 由用户在管理端自行填写 tcp://host:port

# 兼容旧名（live 默认）
REDIS_CHANNEL_KEY = LEGACY_CHANNEL_KEY
REDIS_CTP_FRONT_KEY = "market:ctp_md_front"
# ctp_sim 渠道配置镜像（管理端保存 → bridge 启动时读取覆盖 env）
CTP_SIM_CONFIG_KEY = "market:ctp_sim:config"
QUOTE_KEY_PREFIX = "market:quote:"


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def normalize_channel(value: str | None) -> str:
    """非法值回退 sina"""
    v = str(value or "").strip().lower()
    if v in VALID_CHANNELS:
        return v
    return CHANNEL_SINA


def is_ctp_channel(channel: str | None) -> bool:
    """是否为 CTP bridge 行情渠道"""
    return normalize_channel(channel) in CTP_CHANNELS


def resolve_ctp_md_front(channel: str | None) -> str:
    """按渠道解析行情前置（SimNow 已下线；未知渠道返回空串）"""
    ch = normalize_channel(channel)
    if ch == CHANNEL_REAL_CTP:
        return _env("REAL_CTP_MD_FRONT", "")
    if ch == CHANNEL_CTP_SIM:
        return (
            _env("CTP_SIM_MD_FRONT", DEFAULT_CTP_SIM_MD_FRONT)
            or DEFAULT_CTP_SIM_MD_FRONT
        )
    if ch == CHANNEL_OPENCTP_724:
        return (
            _env("OPENCTP_MD_FRONT_724", DEFAULT_OPENCTP_MD_FRONT_724)
            or DEFAULT_OPENCTP_MD_FRONT_724
        )
    if ch == CHANNEL_OPENCTP_SIM:
        return (
            _env("OPENCTP_MD_FRONT_SIM", DEFAULT_OPENCTP_MD_FRONT_SIM)
            or DEFAULT_OPENCTP_MD_FRONT_SIM
        )
    return ""


def resolve_ctp_login(channel: str | None) -> dict[str, str]:
    """按渠道解析 CTP 登录参数（纯函数，读环境变量）"""
    ch = normalize_channel(channel)
    if ch == CHANNEL_REAL_CTP:
        # 真实期货公司 CTP：broker/investor/前置由 REAL_CTP_* 提供（Level-1 行情免费）
        return {
            "broker_id": _env("REAL_CTP_BROKER_ID", "") or "",
            "investor_id": _env("REAL_CTP_INVESTOR_ID", "") or "",
            "password": _env("REAL_CTP_PASSWORD", ""),
            "app_id": _env("REAL_CTP_APP_ID", ""),
            "auth_code": _env("REAL_CTP_AUTH_CODE", ""),
            "user_product_info": (
                _env("REAL_CTP_USER_PRODUCT_INFO", "qihuo_real") or "qihuo_real"
            )[:10],
            "md_front": resolve_ctp_md_front(ch),
            "channel": CHANNEL_REAL_CTP,
        }
    if ch in (CHANNEL_OPENCTP_724, CHANNEL_OPENCTP_SIM):
        # 7×24 与仿真可用不同账号；默认用各自 env
        if ch == CHANNEL_OPENCTP_SIM:
            investor = _env("OPENCTP_SIM_INVESTOR") or _env(
                "OPENCTP_INVESTOR_ID", "18962"
            )
            password = _env("OPENCTP_SIM_PASSWORD") or _env(
                "OPENCTP_PASSWORD", "123456"
            )
        else:
            investor = _env("OPENCTP_724_INVESTOR") or _env(
                "OPENCTP_INVESTOR_ID", "19659"
            )
            password = _env("OPENCTP_724_PASSWORD") or _env(
                "OPENCTP_PASSWORD", "123456"
            )
        return {
            "broker_id": _env("OPENCTP_BROKER_ID", "9999") or "9999",
            "investor_id": investor,
            "password": password,
            "app_id": _env("OPENCTP_APP_ID", ""),
            "auth_code": _env("OPENCTP_AUTH_CODE", ""),
            "user_product_info": (_env("OPENCTP_USER_PRODUCT_INFO", "qihuo_oc") or "qihuo_oc")[
                :10
            ],
            "md_front": resolve_ctp_md_front(ch),
            "channel": ch,
        }

    if ch == CHANNEL_CTP_SIM:
        # CTP 中转模拟：任意账号密码可登录（2026-10-11 实测），默认占位值即可
        return {
            "broker_id": _env("CTP_SIM_BROKER_ID", "9999") or "9999",
            "investor_id": _env("CTP_SIM_INVESTOR_ID", "qihuo_sim") or "qihuo_sim",
            "password": _env("CTP_SIM_PASSWORD", "qihuo_sim") or "qihuo_sim",
            "app_id": "",
            "auth_code": "",
            "user_product_info": (
                _env("CTP_SIM_USER_PRODUCT_INFO", "qihuo_sim") or "qihuo_sim"
            )[:10],
            "md_front": resolve_ctp_md_front(ch),
            "channel": ch,
        }

    # SimNow 已下线：剩余渠道（sina/vvtr/非法值）非 CTP，返回空登录参数
    return {
        "broker_id": "",
        "investor_id": "",
        "password": "",
        "app_id": "",
        "auth_code": "",
        "user_product_info": "",
        "md_front": "",
        "channel": ch,
    }


async def read_channel_from_redis(
    redis_conn: aioredis.Redis,
    trading_mode: str = MODE_LIVE,
) -> str:
    """读 Redis 镜像渠道；virtual 固定 openctp_724；live 兼容旧键"""
    if trading_mode == MODE_VIRTUAL:
        return CHANNEL_OPENCTP_724
    try:
        raw = await redis_conn.get(channel_key(MODE_LIVE))
        if raw is None:
            raw = await redis_conn.get(LEGACY_CHANNEL_KEY)
    except Exception:
        logger.warning("读取 market channel 失败", exc_info=True)
        return CHANNEL_SINA
    if raw is None:
        return CHANNEL_SINA
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    return normalize_channel(str(raw))


async def write_channel_to_redis(redis_conn: aioredis.Redis, channel: str) -> None:
    """admin 更新后写入 live 渠道镜像（不影响 virtual）"""
    ch = normalize_channel(channel)
    await redis_conn.set(channel_key(MODE_LIVE), ch)
    await redis_conn.set(LEGACY_CHANNEL_KEY, ch)
    if is_ctp_channel(ch):
        front = resolve_ctp_md_front(ch)
        await redis_conn.set(ctp_front_key(MODE_LIVE), front)
        await redis_conn.set(REDIS_CTP_FRONT_KEY, front)
    # virtual 渠道固定
    await redis_conn.set(channel_key(MODE_VIRTUAL), CHANNEL_OPENCTP_724)


async def write_ctp_sim_config_to_redis(
    redis_conn: aioredis.Redis,
    *,
    md_front: str,
    broker_id: str,
    investor_id: str,
    password: str,
) -> None:
    """管理端保存 ctp_sim 配置后镜像到 Redis（bridge 重启时读取）。

    密码仅存内网 Redis（不进日志/接口）；空字段由 bridge 侧过滤回退 env。
    """
    cfg = {
        "md_front": str(md_front or "").strip(),
        "broker_id": str(broker_id or "").strip(),
        "investor_id": str(investor_id or "").strip(),
        "password": str(password or ""),
    }
    await redis_conn.set(CTP_SIM_CONFIG_KEY, json.dumps(cfg, ensure_ascii=False))


async def collect_quotes_from_redis(
    redis_conn: aioredis.Redis,
    trading_mode: str = MODE_LIVE,
) -> list[dict[str, Any]]:
    """从 bridge 写入的 mode 命名空间收集行情（live 回落旧键）"""
    from app.services.market_keys import get_quote_json

    quotes: list[dict[str, Any]] = []
    for contract in ALL_CONTRACTS:
        symbol = contract["symbol"]
        data = await get_quote_json(redis_conn, trading_mode, symbol)
        if data is not None:
            quotes.append(data)
    return filter_valid_quotes(quotes)


async def collect_source_quotes_from_redis(
    redis_conn: aioredis.Redis,
    source: str,
) -> list[dict[str, Any]]:
    """读取 live 原始行情源快照，不回落到统一 quote。

    只做有效性门控（symbol + 正 last_price），不跑 normalize：
    normalize 会把缺失的 pre_close 填成 last，merge 就无法再逐字段
    兜底新浪昨结 → 涨跌恒为 0%（VVTR 源无昨结字段时的实修）。
    字段补全统一交给 merge_live_quote 末尾的 normalize。
    """
    quotes: list[dict[str, Any]] = []
    for contract in ALL_CONTRACTS:
        raw = await redis_conn.get(source_quote_key(source, contract["symbol"]))
        if not raw:
            continue
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="ignore")
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            quotes.append(data)
    return [q for q in quotes if is_valid_quote(q)]


# ========== sina 源键 ==========
# 新浪行情由独立容器 qihuo-sina-bridge 轮询写源键（TTL 7d，带 recv_ts），
# 本进程只读不拉（新浪出网集中在桥容器；轮询节奏自适应——推送源供数 60s、
# 交易时段 3s、休市 60s——见 app/connectors/sina/bridge.py）。
# 源键缺失/陈旧由 merge 层 90s freshness 判定自然表达，无需本层兜底。


async def fetch_quotes_for_channel(
    redis_conn: aioredis.Redis,
    channel: str | None = None,
    trading_mode: str = MODE_LIVE,
) -> list[dict[str, Any]]:
    """按渠道 + 交易盘拉取主行情列表"""
    # 惰性套用 VVTR 主力合约快照（5 分钟检查一次；换月后消费端即时跟进）
    from app.services.contracts_sync import ensure_active_contracts

    try:
        await ensure_active_contracts(redis_conn)
    except Exception:
        logger.debug("主力合约快照套用异常", exc_info=True)

    if trading_mode == MODE_VIRTUAL:
        # 虚拟盘只读 openctp 7×24 bridge 写入的 virtual 命名空间
        quotes = await collect_quotes_from_redis(redis_conn, MODE_VIRTUAL)
        if not quotes:
            logger.warning(
                "virtual 盘无 Redis 行情（请确认 qihuo-ctp-bridge-virtual 已启动）"
            )
        return quotes

    # 探测推送源（vvtr WS 桥）是否在实际供数：
    # 供数时 sina 源键仅作昨结/名称等日内静态字段兜底；断流时 sina 即主源。
    active_channel = channel
    if active_channel is None:
        active_channel = await read_channel_from_redis(redis_conn, MODE_LIVE)
    push_quotes: list[dict[str, Any]] = []
    push_source = ""
    if active_channel == CHANNEL_VVTR:
        from app.connectors.vvtr.config import read_vvtr_state_from_redis

        state = await read_vvtr_state_from_redis(redis_conn)
        if state.get("enabled") and state.get("quotes_enabled"):
            push_quotes = await collect_source_quotes_from_redis(
                redis_conn, CHANNEL_VVTR
            )
            push_source = CHANNEL_VVTR
            if not push_quotes:
                logger.debug("VVTR 渠道源键为空，本轮以 sina 源键为主")
    # sina 源键（qihuo-sina-bridge 写入）：读 Redis，本进程不出网。
    # 盘口深度 5 档 → 1 档（sina 仅 bid1/ask1）。
    sina_quotes = await collect_source_quotes_from_redis(redis_conn, CHANNEL_SINA)

    from app.services.market_quote_merge import merge_live_quotes

    if push_quotes:
        # 推送源（vvtr 五档盘口 + 交易所 OHLC）作主源，sina 逐字段兜底
        return merge_live_quotes(
            push_quotes, sina_quotes, primary_source=push_source
        )

    # ctp_sim（CTP 中转模拟，休市联调用）：桥源键作主源，sina 逐字段兜底
    # 静态字段（昨结/名称）。快照源 recv_ts 不刷新时 merge 自动回落 sina。
    if active_channel == CHANNEL_CTP_SIM:
        sim_quotes = await collect_source_quotes_from_redis(
            redis_conn, CHANNEL_CTP_SIM
        )
        if sim_quotes:
            return merge_live_quotes(
                sim_quotes, sina_quotes, primary_source=CHANNEL_CTP_SIM
            )
        logger.debug("ctp_sim 渠道源键为空，本轮以 sina 源键为主")

    # simnow 主源已停用，传空列表 → merge 自动以 sina 为主
    quotes = merge_live_quotes([], sina_quotes)
    return quotes
