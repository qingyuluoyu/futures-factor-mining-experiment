"""渠道故障转移 —— 健康监控、主备切换编排、bridge 重启、开盘前自检

- ``channel_health_monitor_loop``：每 10s 探测主备渠道健康，写 Redis 状态键
  + 维护 ``market:live:active_channel``；主渠道持续 red 则重启其 bridge。
- ``channel_preflight_loop``：每天 09:20（开盘前 10 分钟）探测全部候选渠道，
  red 的重启 bridge 并把结果写入 Redis 供后台展示。

依赖 backend 容器挂载 /var/run/docker.sock + 镜像装 aiodocker。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.channel_health import (
    collect_channels_health,
    decide_active_channel,
)
from app.services.market_channel import (
    CHANNEL_CTP_SIM,
    CHANNEL_OPENCTP_724,
    CHANNEL_REAL_CTP,
    CHANNEL_SINA,
    normalize_channel,
    read_channel_from_redis,
    write_channel_to_redis,
)

logger = logging.getLogger("qihuo.channel_failover")

_BJ_TZ = timezone(timedelta(hours=8))
_HEALTH_INTERVAL = 10  # 健康监控间隔（秒）
_PREFLIGHT_HOUR = 9  # 开盘前自检 09:20
_PREFLIGHT_MINUTE = 20
_RED_RESTART_THRESHOLD = 6  # 主渠道连续 red 6 次（~60s）才重启，防抖
_RESTART_COOLDOWN_SEC = 300  # 重启成功后 5 分钟内不再重启（防午休/登录抖动连环重启）

ACTIVE_CHANNEL_KEY = "market:live:active_channel"
HEALTH_KEY_PREFIX = "market:channel:health:"
PREFLIGHT_KEY = "market:channel:preflight:last"

# 已部署渠道 → bridge 容器名（未列出的渠道无独立容器，重启为无操作）
CHANNEL_TO_CONTAINER: dict[str, str] = {
    CHANNEL_OPENCTP_724: "qihuo-ctp-bridge-virtual",
    CHANNEL_REAL_CTP: "qihuo-ctp-bridge",
    # ctp_sim 复用 live bridge 容器（单容器单渠道，重启时按 Redis 渠道重建会话）
    CHANNEL_CTP_SIM: "qihuo-ctp-bridge",
}


async def restart_bridge_container(channel: str) -> dict[str, Any]:
    """重启渠道对应的 bridge 容器（卡死恢复）。返回 {ok, channel, container, detail}"""
    container_name = CHANNEL_TO_CONTAINER.get(channel)
    if not container_name:
        return {
            "ok": False,
            "channel": channel,
            "container": None,
            "detail": "该渠道无 bridge 容器（HTTP 源或未部署）",
        }
    try:
        import aiodocker  # type: ignore
    except ImportError:
        logger.error("aiodocker 未安装，无法重启 bridge")
        return {
            "ok": False,
            "channel": channel,
            "container": container_name,
            "detail": "aiodocker 未安装",
        }
    try:
        async with aiodocker.Docker() as docker:
            container = docker.containers.container(container_name)
            await container.restart(timeout=10)
        logger.warning(
            "已重启 bridge channel=%s container=%s", channel, container_name
        )
        return {
            "ok": True,
            "channel": channel,
            "container": container_name,
            "detail": "已重启",
        }
    except Exception as exc:  # noqa: BLE001 — 重启失败兜底返回，不抛
        logger.error("重启 bridge 失败 channel=%s: %s", channel, exc, exc_info=True)
        return {
            "ok": False,
            "channel": channel,
            "container": container_name,
            "detail": f"重启失败: {type(exc).__name__}",
        }


async def read_primary_backup(session_factory: Any) -> tuple[str, str, bool]:
    """读主/备渠道配置 + 自动开关（primary, backup, auto_switch）"""
    from app.services.system_settings_svc import (
        ensure_system_settings,
        settings_to_dict,
    )

    async with session_factory() as session:
        row = await ensure_system_settings(session)
        d = settings_to_dict(row)
    primary = normalize_channel(d.get("market_data_channel") or "sina")
    backup = normalize_channel(d.get("backup_channel") or "sina")
    return primary, backup, bool(d.get("channel_auto_switch", True))


async def write_health_to_redis(
    redis_conn: Any, healths: dict[str, dict]
) -> None:
    """各渠道健康写 Redis（TTL 120s，过期即视为久未更新）"""
    pipe = redis_conn.pipeline()
    for ch, h in healths.items():
        pipe.set(
            f"{HEALTH_KEY_PREFIX}{ch}",
            json.dumps(h, ensure_ascii=False),
            ex=120,
        )
    await pipe.execute()


async def read_active_channel(redis_conn: Any) -> str | None:
    """当前生效渠道（无则 None）"""
    raw = await redis_conn.get(ACTIVE_CHANNEL_KEY)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="ignore")
    return str(raw)


# bridge 自愈 / 容器异常 检测的跨轮记忆（_detect_bridge_events 维护）
# {mode: 上次见到的 market:{mode}:bridge:last_exit ts}；首次只记录不告警，避免启动误报
_last_seen_exit_ts: dict[str, float] = {}
# {mode: 上次心跳 status(green/orange/red)}；用于 red 跳变检测
_last_bridge_status: dict[str, str] = {}


async def _detect_bridge_events(redis_conn: Any) -> None:
    """检测 bridge 自愈(last_exit 变化)与容器异常(心跳 red 跳变)，代发 ops_alert。

    bridge 是独立进程，自愈 os._exit 前只来得及写 market:{mode}:bridge:last_exit；
    本函数由 backend monitor loop 每 10s 调用，发现新事件后代发告警。
    bridge 崩溃(来不及写 last_exit)则靠心跳变 red 检测。
    """
    import time as _t

    from app.connectors.ctp.bridge_heartbeat import parse_heartbeat
    from app.services.ops_alert import bridge_last_exit_key, push_ops_alert

    for mode in ("live", "virtual"):
        # 心跳 status（green<=30 / orange<=90 / red>90，仿 channel_health 阈值）
        raw_hb = await redis_conn.get(f"market:{mode}:bridge:heartbeat")
        hb = parse_heartbeat(raw_hb)
        age = float(hb.get("age_sec") or 9999) if hb else 9999.0
        status = "green" if age <= 30 else ("orange" if age <= 90 else "red")

        # 自愈检测：last_exit ts 变化（首次只记录不告警，跳过启动见到的存量）
        exit_info: dict | None = None
        exit_ts = 0.0
        try:
            raw_exit = await redis_conn.get(bridge_last_exit_key(mode))
            if raw_exit:
                exit_info = json.loads(raw_exit)
                exit_ts = float(exit_info.get("ts") or 0)
        except Exception:
            pass
        last_seen = _last_seen_exit_ts.get(mode, 0.0)
        if exit_ts > last_seen:
            _last_seen_exit_ts[mode] = exit_ts
            if last_seen > 0 and exit_info:
                await push_ops_alert(
                    redis_conn,
                    "warning",
                    "bridge_self_heal",
                    f"{mode} bridge 自愈重启（{exit_info.get('reason', 'unknown')}）",
                    exit_info,
                    dedupe_sec=30,
                )

        # 容器异常检测：心跳 red 跳变（上次非 red → 本次 red，且非刚自愈）
        prev_status = _last_bridge_status.get(mode)
        _last_bridge_status[mode] = status
        if (
            status == "red"
            and prev_status
            and prev_status != "red"
            and not (exit_ts > 0 and (_t.time() - exit_ts) < 60)
        ):
            await push_ops_alert(
                redis_conn,
                "error",
                "bridge_down",
                f"{mode} bridge 容器异常（心跳 age {int(age)}s）",
                {"mode": mode, "age_sec": int(age)},
                dedupe_sec=120,
            )


async def sync_consumer_channel_key(redis_conn: Any, active: str) -> bool:
    """把健康决策的 active 渠道同步到消费端键 market:live:channel

    消费端（fetch/K线 forming）读 market:live:channel（兼容旧
    market:channel），与本模块的 ACTIVE_CHANNEL_KEY 记账键不同。
    Redis 被部署重置后消费键缺失会静默回落 sina——监控"切换"了
    但没人听。返回是否发生写入。
    """
    try:
        consumer = await read_channel_from_redis(redis_conn)
        if consumer == active:
            return False
        await write_channel_to_redis(redis_conn, active)
        return True
    except Exception:
        logger.warning("渠道消费键同步失败", exc_info=True)
        return False


async def channel_health_monitor_loop(
    redis_conn: Any,
    session_factory: Any,
) -> None:
    """每 10s 探测渠道健康 + 维护 active_channel + 主 red 持续则重启"""
    red_count = 0
    last_restart_mono = 0.0
    await asyncio.sleep(6)  # 错开启动峰值
    while True:
        try:
            primary, backup, auto = await read_primary_backup(session_factory)
            channels = list({primary, backup})
            healths = await collect_channels_health(redis_conn, channels)
            await write_health_to_redis(redis_conn, healths)

            active = (
                decide_active_channel(healths, primary, backup) if auto else primary
            )
            prev = await read_active_channel(redis_conn)
            await redis_conn.set(ACTIVE_CHANNEL_KEY, active)
            # 同步消费端渠道键（Redis 重置后自愈，防静默回落 sina）
            consumer_fixed = await sync_consumer_channel_key(redis_conn, active)
            if prev is not None and prev != active:
                logger.warning(
                    "行情渠道切换 %s → %s（主=%s 备=%s）",
                    prev,
                    active,
                    primary,
                    backup,
                )
                from app.services.ops_alert import push_ops_alert

                await push_ops_alert(
                    redis_conn,
                    "info",
                    "channel_switch",
                    f"行情渠道切换 {prev} → {active}",
                    {"prev": prev, "active": active, "primary": primary, "backup": backup},
                    dedupe_sec=0,
                )
            elif consumer_fixed:
                logger.warning(
                    "行情渠道消费键缺失/不一致，已同步为 %s（主=%s 备=%s）",
                    active,
                    primary,
                    backup,
                )

            # 主渠道持续 red → 重启（防抖 + 冷却，避免午休/登录抖动连环重启）
            now_mono = time.monotonic()
            in_cooldown = (now_mono - last_restart_mono) < _RESTART_COOLDOWN_SEC
            if healths.get(primary, {}).get("status") == "red":
                red_count += 1
                if red_count >= _RED_RESTART_THRESHOLD and not in_cooldown:
                    logger.warning("主渠道 %s 持续 red，重启", primary)
                    if (await restart_bridge_container(primary)).get("ok"):
                        red_count = 0
                        last_restart_mono = now_mono
                elif in_cooldown and red_count >= _RED_RESTART_THRESHOLD:
                    logger.info(
                        "主渠道 %s 仍 red，但重启冷却中（%ss）",
                        primary,
                        int(_RESTART_COOLDOWN_SEC - (now_mono - last_restart_mono)),
                    )
                    red_count = 0
            else:
                red_count = 0
            # bridge 自愈 / 容器异常检测（每轮独立于渠道 red 计数）
            await _detect_bridge_events(redis_conn)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("渠道健康监控循环异常")
        await asyncio.sleep(_HEALTH_INTERVAL)


async def channel_preflight_loop(
    redis_conn: Any,
    session_factory: Any,
) -> None:
    """每天 09:20 自检全部候选渠道，red 的重启 + 结果写 Redis"""
    while True:
        try:
            now = datetime.now(_BJ_TZ)
            target = now.replace(
                hour=_PREFLIGHT_HOUR,
                minute=_PREFLIGHT_MINUTE,
                second=0,
                microsecond=0,
            )
            if now >= target:
                target = target + timedelta(days=1)
            await asyncio.sleep(max(1.0, (target - now).total_seconds()))

            primary, backup, _auto = await read_primary_backup(session_factory)
            channels = list({primary, backup, CHANNEL_SINA})
            healths = await collect_channels_health(redis_conn, channels)
            await write_health_to_redis(redis_conn, healths)

            restarted: list[dict[str, Any]] = []
            for ch, h in healths.items():
                if h.get("status") == "red":
                    logger.warning("开盘前自检：%s red，重启", ch)
                    restarted.append({"channel": ch, **await restart_bridge_container(ch)})
            await redis_conn.set(
                PREFLIGHT_KEY,
                json.dumps(
                    {
                        "ts": datetime.now(_BJ_TZ).isoformat(),
                        "restarted": restarted,
                    },
                    ensure_ascii=False,
                ),
                ex=7 * 24 * 3600,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("渠道开盘前自检循环异常")
