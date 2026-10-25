"""VVTR 主力合约同步 —— realSymbol → 运行时刷新 ALL_CONTRACTS + 过半自动换月

背景：静态合约表手工维护月份，换月后滞后（如 pp2609→pp2701 期间，
WSS 桥已按主力识别缓存订阅新主力并写入源键，但消费端 collect/新浪
清单/前端合约列表全部读静态旧表，导致前台看不到新主力的 1s WSS 数据）。

机制：
- master worker：逐品种调 /realSymbol（走 http_client 共享 1 rps 节流），
  apply_active_contracts 原地换月 + 写 Redis 快照（TTL 7 天）；
- 月份只进不退（later_symbol）：VVTR 主力未跟上已迁移表时不回退。
  【2026-09-02 事故教训：曾尝试让常规同步"双向跟随 9999 纠偏过冲"，
  实测 VVTR realSymbol 整体滞后平台迁移基线一代（9999 返回 rb2610/
  au2610 等旧主力），双向跟随把全表倒退一代——只进不退必须保留；
  过冲（如 M 的 m2705）靠静态表人工纠正，勿再引入回移逻辑】；
- 自动换月（roll_window_open）：合约交割前月 15 日后（"最后一个月
  过半"）追加查询次主力 9998 前进，无需人工脚本；次主力未转正时
  12h 周期重试自动跟上；
- 其余 worker/进程：fetch 热路径每 5 分钟惰性读快照套用（ensure_…），
  快照跨部署存活，重启后数秒内即恢复最新主力。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("qihuo.contracts_sync")

_BJ_TZ = timezone(timedelta(hours=8))

# 快照键：{ts, entries:[{symbol,code,name,exchange}]}
ACTIVE_SNAPSHOT_KEY = "market:contracts:active:v1"
_SNAPSHOT_TTL = 7 * 24 * 3600
# master 刷新周期（换月以月计，12h 足够；失败下轮重试）
REFRESH_INTERVAL = 12 * 3600
# 非 master 进程惰性检查周期
_APPLY_CHECK_INTERVAL = 300.0

# 进程内状态（monotonic 控检查频率，wall time 记已套用快照版本）
_state: dict[str, float] = {"last_check": 0.0, "applied_ts": 0.0}


def _month_code(symbol: str) -> int:
    """合约月份码（rb2701 → 2701）；无数字返回 0"""
    digits = "".join(ch for ch in str(symbol or "") if ch.isdigit())
    return int(digits) if digits else 0


def later_symbol(current: str, incoming: str) -> str:
    """取月份更靠后的合约：月份只进不退

    防回退：VVTR 主力尚未跟上已迁移的表（如表 pp2701、主力仍 pp2609）
    时保持现值，真正换月后自然对齐；相等/无法比较时优先 incoming
    （以 VVTR 返回为准）。VVTR 9999 滞后是常态（2026-09-02 实测全表
    滞后一代），回移永远不是"纠偏"。
    """
    mc, mi = _month_code(current), _month_code(incoming)
    if not mi:
        return current
    if not mc:
        return incoming
    return incoming if mi > mc else current



def roll_window_open(symbol: str, now: datetime | None = None) -> bool:
    """自动换月窗口：合约「最后一个月」过半（交割前月 15 日之后）

    纯时间规则（无硬编码月份）：2609（9 月交割）→ 8 月 16 日起触发，
    2610 → 9 月 16 日，2701 → 上年 12 月 16 日。窗口期内常态同步
    追加查询 VVTR 次主力前进；次主力尚未转正时 later_symbol 挡住
    回退，12h 周期重试直到跟上。
    """
    digits = "".join(ch for ch in str(symbol or "") if ch.isdigit())
    if len(digits) < 4:
        return False
    yy, mm = int(digits[-4:-2]), int(digits[-2:])
    year, month = 2000 + yy, mm
    roll_year, roll_month = (year - 1, 12) if month == 1 else (year, month - 1)
    bj = now or datetime.now(_BJ_TZ)
    if (bj.year, bj.month) > (roll_year, roll_month):
        return True
    if (bj.year, bj.month) == (roll_year, roll_month):
        return bj.day > 15
    return False


async def _collect_codes() -> list[str]:
    """合约表品种清单（去重保序）"""
    from app.data.contracts import ALL_CONTRACTS

    codes: list[str] = []
    seen: set[str] = set()
    for c in ALL_CONTRACTS:
        code = str(c.get("code") or "").strip().upper()
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


async def _resolve_targets(
    redis_conn: aioredis.Redis | None,
    session_factory: Any,
    suffixes: tuple[str, ...] = ("9999",),
    now: datetime | None = None,
) -> dict[str, str]:
    """逐品种 realSymbol → 目标合约（月份只进不退 + 过半自动换月）

    suffixes：9999 主力 / 9998 次主力；多个后缀时取月份最大者
    （滚动期次主力按成交量可能是"往回"的月份，如 al2610 主力时
    次主力 al2609，取最大避免回退）。
    自动换月：目标合约进入换月窗口（交割前月 15 日后）时追加查询
    9998 次主力前进，无需人工迁移脚本；次主力未转正时 later_symbol
    挡住回退，12h 同步重试自动跟上。
    锚点为当前有效表：先套用 Redis 快照（脚本/独立进程的内存表
    还是静态旧月份，不套用会以错误基准计算）。
    返回 {code: 目标}。
    """
    from asyncio import to_thread

    # 锚定有效表：套用快照（进程内已是最新时空操作）
    await ensure_active_contracts(redis_conn)

    from app.connectors.vvtr.config import (
        _load_config_from_pg,
        get_vvtr_runtime_config,
    )
    from app.connectors.vvtr.http_client import fetch_vvtr_real_master
    from app.data.contracts import ALL_CONTRACTS

    if session_factory is not None:
        try:
            cfg = await get_vvtr_runtime_config(session_factory)
        except Exception:
            logger.warning("VVTR 配置读取失败，跳过主力同步", exc_info=True)
            return {}
    else:
        # 无 session factory（脚本/独立运行）：asyncpg 直读配置
        try:
            cfg = await _load_config_from_pg()
        except Exception:
            logger.warning("VVTR 配置直读失败，跳过主力同步", exc_info=True)
            return {}
    if not cfg.api_key:
        return {}

    # 锚点取每品种首行（主力行）：附加观察合约行（如 M 的 m2705）
    # 不能当锚点，否则 VVTR 返回更低月份时 later_symbol 误判"前进"，
    # 会把主力行改到附加月份上
    static_by_code: dict[str, str] = {}
    for c in ALL_CONTRACTS:
        code_u = str(c.get("code") or "").upper()
        if code_u and code_u not in static_by_code:
            static_by_code[code_u] = str(c.get("symbol") or "").lower()
    targets: dict[str, str] = {}
    for code in await _collect_codes():
        current = static_by_code.get(code, "")
        merged = current
        queried: set[str] = set(suffixes)
        for suffix in suffixes:
            rsym = await to_thread(
                fetch_vvtr_real_master,
                cfg.api_key,
                code.lower(),
                cfg.base_url,
                suffix,
            )
            if rsym:
                merged = later_symbol(merged, rsym)
        # 换月窗口（交割前月过半）自动追加次主力
        if "9998" not in queried and roll_window_open(merged, now):
            rsym = await to_thread(
                fetch_vvtr_real_master,
                cfg.api_key,
                code.lower(),
                cfg.base_url,
                "9998",
            )
            if rsym:
                merged = later_symbol(merged, rsym)
        if merged and (not current or merged != current):
            targets[code] = merged
    return targets


async def _apply_and_snapshot(
    redis_conn: aioredis.Redis,
    targets: dict[str, str],
    label: str,
) -> int:
    """目标合约 → apply_active_contracts + 写快照；返回变更条数"""
    from app.data.contracts import ALL_CONTRACTS, apply_active_contracts, get_product_name

    # 首行优先（主力行元数据），同品种附加观察行不覆盖
    static_by_code: dict[str, dict[str, str]] = {}
    for c in ALL_CONTRACTS:
        code_u = str(c.get("code") or "").upper()
        if code_u and code_u not in static_by_code:
            static_by_code[code_u] = c
    entries: list[dict[str, str]] = []
    for code, sym in targets.items():
        base = static_by_code.get(code) or {}
        digits = "".join(ch for ch in sym if ch.isdigit())
        product = get_product_name(code)
        entries.append(
            {
                "symbol": sym,
                "code": code,
                "name": (
                    f"{product}{digits}"
                    if product and digits
                    else str(base.get("name") or sym)
                ),
                "exchange": str(base.get("exchange") or ""),
            }
        )
    changed = apply_active_contracts(entries)
    ts = time.time()
    _state["applied_ts"] = ts
    # 快照必须含全量表：新进程以静态表为底套用，部分快照会让
    # 未包含的品种回落到旧月份
    full_entries = [
        {
            "symbol": str(c.get("symbol") or ""),
            "code": str(c.get("code") or ""),
            "name": str(c.get("name") or ""),
            "exchange": str(c.get("exchange") or ""),
        }
        for c in ALL_CONTRACTS
        if c.get("symbol") and c.get("code")
    ]
    try:
        await redis_conn.set(
            ACTIVE_SNAPSHOT_KEY,
            json.dumps(
                {"ts": ts, "entries": full_entries}, ensure_ascii=False
            ),
            ex=_SNAPSHOT_TTL,
        )
    except Exception:
        logger.warning("主力合约快照写入失败", exc_info=True)
    sample = ",".join(
        f"{c}→{s}" for c, s in list(targets.items())[:4]
    )
    logger.info(
        "VVTR %s 完成：%d/%d 品种，更新 %d 条合约（%s…）",
        label,
        len(targets),
        len(static_by_code),
        changed,
        sample,
    )
    return changed


async def refresh_masters_from_vvtr(
    redis_conn: aioredis.Redis,
    session_factory: Any,
) -> dict[str, str]:
    """master：逐品种 realSymbol(9999) → 刷新合约表 + 写快照

    月份只进不退：VVTR 主力月份 ≥ 表当前才前进，不回退。
    返回 {code: 目标合约}。
    """
    targets = await _resolve_targets(redis_conn, session_factory, ("9999",))
    if not targets:
        logger.warning("VVTR 主力同步无结果（realSymbol 全部失败？），保留现有合约表")
        return {}
    await _apply_and_snapshot(redis_conn, targets, "主力同步")
    return targets


async def migrate_to_next_contracts(
    redis_conn: aioredis.Redis,
    session_factory: Any,
) -> dict[str, str]:
    """一次性迁移：跳到下一流动性合约（主力+次主力取月份最大）

    换月前的提前迁移（如 pp2609→pp2701、if2609→if2612、欧线/月度
    品种由 VVTR 按真实成交量给出）；滚动期次主力可能是"往回"的月份
    （al2610 主力、次主力 al2609），故与主力一起取月份最大者。
    月份只进不退，可重复执行（幂等）。
    """
    targets = await _resolve_targets(
        redis_conn, session_factory, ("9999", "9998")
    )
    if not targets:
        logger.warning("VVTR 次主力迁移无结果，保留现有合约表")
        return {}
    await _apply_and_snapshot(redis_conn, targets, "次主力迁移")
    return targets


async def ensure_active_contracts(redis_conn: aioredis.Redis | None) -> None:
    """任意进程：惰性读快照套用（5 分钟检查一次；静默失败保留静态表）"""
    now = time.monotonic()
    if now - _state["last_check"] < _APPLY_CHECK_INTERVAL:
        return
    _state["last_check"] = now
    if redis_conn is None:
        return
    try:
        raw = await redis_conn.get(ACTIVE_SNAPSHOT_KEY)
        if not raw:
            return
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        data = json.loads(raw)
        ts = float(data.get("ts") or 0)
        if ts <= _state["applied_ts"]:
            return
        entries = data.get("entries") or []
        from app.data.contracts import apply_active_contracts

        n = apply_active_contracts(entries)
        _state["applied_ts"] = ts
        if n:
            logger.info("已套用 VVTR 主力合约快照（%d 条变更）", n)
    except Exception:
        logger.debug("主力合约快照套用失败", exc_info=True)


def reset_process_state() -> None:
    """重置进程内套用状态（测试用）"""
    _state["last_check"] = 0.0
    _state["applied_ts"] = 0.0
