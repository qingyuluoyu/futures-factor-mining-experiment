"""管理后台 API —— 用户 CRUD、注册/模拟资金设置、用户交易与 AI 查看"""

import json
from asyncio import to_thread
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_admin, get_session
from app.core.trading_mode import normalize_trading_mode
from app.models.user import User
from app.schemas.admin import (
    AdminUserCreateRequest,
    AdminUserUpdateRequest,
    SystemSettingsUpdateRequest,
)
from app.services.admin import user_inspect_svc, users_svc
from app.services.market_channel import write_channel_to_redis
from app.services.system_settings_svc import (
    ensure_system_settings,
    settings_to_dict,
    update_system_settings,
)

router = APIRouter(prefix="/api/admin", tags=["管理后台"])


def _mode_q(trading_mode: str) -> str:
    return normalize_trading_mode(trading_mode)


# ---------- 系统设置 ----------


@router.get("/settings")
async def admin_get_settings(
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """获取系统设置（注册策略 + 模拟资金）"""
    row = await ensure_system_settings(session)
    return settings_to_dict(row)


@router.put("/settings")
async def admin_update_settings(
    body: SystemSettingsUpdateRequest,
    request: Request,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """更新系统设置；行情渠道同步到 Redis market:channel（仅实盘）"""
    row = await update_system_settings(session, body.model_dump(exclude_unset=True))
    payload = settings_to_dict(row)
    redis_conn = getattr(request.app.state, "redis", None)
    if redis_conn is not None:
        channel = str(payload.get("market_data_channel") or "sina")
        await write_channel_to_redis(redis_conn, channel)
        # K 线历史源配置镜像（主备/轮询 + 源列表）
        from app.services.kline_source_cfg import write_kline_source_config_to_redis

        await write_kline_source_config_to_redis(redis_conn, payload)
        # 行情/K线刷新间隔镜像（默认 + per-channel）
        from app.services.refresh_cfg import write_refresh_intervals_to_redis

        await write_refresh_intervals_to_redis(
            redis_conn,
            payload.get("market_refresh_interval_sec", 1),
            payload.get("market_refresh_intervals"),
        )
        # VVTR 开关镜像（1s 热路径读取）+ 配置缓存失效（桥 10s 内重连/休眠）
        from app.connectors.vvtr.config import (
            invalidate_async_cache,
            write_vvtr_state_to_redis,
        )

        await write_vvtr_state_to_redis(
            redis_conn,
            enabled=bool(payload.get("vvtr_enabled")),
            quotes_enabled=bool(payload.get("vvtr_quotes_enabled")),
            kline_enabled=bool(payload.get("vvtr_kline_enabled")),
        )
        invalidate_async_cache()
        # ctp_sim 渠道配置镜像（bridge 重启时读取覆盖 env 默认）
        from app.services.crypto import decrypt
        from app.services.market_channel import write_ctp_sim_config_to_redis

        sim_password = ""
        raw_pw = str(getattr(row, "ctp_sim_password", "") or "").strip()
        if raw_pw:
            try:
                sim_password = decrypt(raw_pw)
            except Exception:
                sim_password = ""
        await write_ctp_sim_config_to_redis(
            redis_conn,
            md_front=str(payload.get("ctp_sim_md_front") or ""),
            broker_id=str(payload.get("ctp_sim_broker_id") or ""),
            investor_id=str(payload.get("ctp_sim_investor_id") or ""),
            password=sim_password,
        )
    return payload


# ---------- VVTR 数据源 ----------


class VvtrTestRequest(BaseModel):
    """VVTR 连接测试：优先用提交的密钥，否则用已存储密钥"""

    api_key: str | None = Field(default=None, max_length=256)
    mobile: str | None = Field(default=None, max_length=32)


@router.post("/vvtr/test")
async def admin_test_vvtr(
    body: VvtrTestRequest,
    request: Request,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """测试 VVTR 连接：调 /my/permissions 校验密钥并回显套餐/权限

    密钥来源：请求体明文（保存前试连）→ DB 已存密钥（AES 解密）。
    响应/日志均不含明文密钥。
    """
    from app.connectors.vvtr.http_client import fetch_vvtr_permissions
    from app.services.crypto import decrypt

    row = await ensure_system_settings(session)
    base_url = str(getattr(row, "vvtr_base_url", "") or "")
    stored_mobile = str(getattr(row, "vvtr_mobile", "") or "")
    api_key = str(body.api_key or "").strip()
    if not api_key:
        raw = str(getattr(row, "vvtr_api_key", "") or "").strip()
        if raw:
            try:
                api_key = decrypt(raw)
            except Exception:
                api_key = ""
    mobile = str(body.mobile or "").strip() or stored_mobile
    if not api_key:
        return {
            "ok": False,
            "code": None,
            "msg": "未提供密钥（请求体或系统设置中均无 apiKey）",
            "packages": [],
            "apis": [],
        }
    result = await to_thread(fetch_vvtr_permissions, api_key, mobile, base_url)
    return result


# ---------- 渠道状态 ----------


@router.get("/channels/status")
async def admin_get_channels_status(
    request: Request,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """渠道健康状态 + 主备配置 + 当前生效渠道（供管理后台仪表盘）"""
    row = await ensure_system_settings(session)
    cfg = settings_to_dict(row)
    redis_conn = getattr(request.app.state, "redis", None)
    channels: dict[str, dict] = {}
    active: str | None = None
    if redis_conn is not None:
        from app.services.channel_failover import (
            ACTIVE_CHANNEL_KEY,
            HEALTH_KEY_PREFIX,
            read_active_channel,
        )

        active = await read_active_channel(redis_conn)
        async for key in redis_conn.scan_iter(f"{HEALTH_KEY_PREFIX}*"):
            name = key.decode() if isinstance(key, bytes) else str(key)
            ch = name.rsplit(":", 1)[-1]
            raw = await redis_conn.get(key)
            try:
                channels[ch] = json.loads(raw) if raw else {}
            except (json.JSONDecodeError, TypeError):
                channels[ch] = {}
    return {
        "active": active or cfg.get("market_data_channel") or "sina",
        "primary": cfg.get("market_data_channel"),
        "backup": cfg.get("backup_channel"),
        "auto_switch": cfg.get("channel_auto_switch", True),
        "channels": channels,
    }


@router.post("/channels/{channel}/restart")
async def admin_restart_channel(
    channel: str,
    _admin: User = Depends(get_current_admin),
) -> dict:
    """手动重启某渠道对应的 bridge 容器"""
    from app.services.channel_failover import restart_bridge_container
    from app.services.market_channel import normalize_channel

    return await restart_bridge_container(normalize_channel(channel))


# ---------- K 线历史数据源状态 ----------


@router.get("/kline-sources/status")
async def admin_get_kline_sources_status(
    request: Request,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """K 线历史源健康状态 + 主备配置（供管理后台仪表盘）

    返回 {primary, backups, sources}：sources 为 {source: health}，
    health 来自后台探测任务写的 kline:source:health:{source}（TTL 120s）。
    """
    from app.services.kline_source_cfg import get_kline_source_config
    from app.services.kline_source_health import HEALTH_KEY_PREFIX

    redis_conn = getattr(request.app.state, "redis", None)
    primary, backups = await get_kline_source_config(redis_conn, session)
    sources: dict[str, dict] = {}
    if redis_conn is not None:
        # 候选源：主 + 备（去重保序）
        seen: set[str] = set()
        candidates: list[str] = []
        for src in [primary, *backups]:
            if src and src not in seen:
                seen.add(src)
                candidates.append(src)
        for src in candidates:
            raw = await redis_conn.get(f"{HEALTH_KEY_PREFIX}:{src}")
            try:
                sources[src] = json.loads(raw) if raw else {}
            except (json.JSONDecodeError, TypeError):
                sources[src] = {}
    return {
        "primary": primary,
        "backups": backups,
        "sources": sources,
    }


# ---------- K 线修正任务状态 ----------


@router.get("/kline-sync/status")
async def admin_get_kline_sync_status(
    request: Request,
    _admin: User = Depends(get_current_admin),
) -> dict:
    """K线修正任务状态 + 最近执行日志（供管理后台展示）

    返回 {status: {...}, logs: [...]}：
    - status: 当前/上次修正的汇总（status/trigger/started_at/duration/stats/errors）
    - logs: 最近 200 条品种级修正日志（ts/code/symbol/periods/status）
    """
    redis_conn = getattr(request.app.state, "redis", None)
    status: dict = {}
    logs: list = []
    if redis_conn is not None:
        try:
            raw_status = await redis_conn.get("kline:sync:status")
            if raw_status:
                status = json.loads(raw_status)
        except Exception:
            pass
        try:
            raw_logs = await redis_conn.lrange("kline:sync:log", 0, 199)
            for raw in raw_logs:
                try:
                    logs.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    continue
        except Exception:
            pass
    return {"status": status, "logs": logs}


# ---------- K 线修正任务组：统一状态 / 启停 / 手动触发 / 日志 ----------


@router.get("/kline-tasks")
async def admin_get_kline_tasks(
    request: Request,
    _admin: User = Depends(get_current_admin),
) -> dict:
    """K 线修正组 5 任务状态聚合(状态快照 + 暂停标志 + 元信息)"""
    redis_conn = getattr(request.app.state, "redis", None)
    from app.services.kline_task_status import (
        KLINE_TASKS,
        read_paused,
        read_status,
    )

    tasks: list[dict] = []
    for name, meta in KLINE_TASKS.items():
        status = await read_status(redis_conn, name) if redis_conn else {}
        paused = await read_paused(redis_conn, name) if redis_conn else False
        tasks.append(
            {
                "name": name,
                "label": meta["label"],
                "interval_desc": meta["interval_desc"],
                "pausable": meta["pausable"],
                "runnable": meta["runnable"],
                "paused": paused,
                "status": status,
            }
        )
    return {"tasks": tasks}


@router.post("/kline-tasks/{name}/pause")
async def admin_pause_kline_task(
    name: str,
    request: Request,
    _admin: User = Depends(get_current_admin),
) -> dict:
    """暂停一个 K 线修正任务(一次性任务不可控)"""
    from app.services.kline_task_status import KLINE_TASKS, set_paused

    meta = KLINE_TASKS.get(name)
    if meta is None:
        raise HTTPException(status_code=404, detail="未知任务")
    if not meta["pausable"]:
        raise HTTPException(status_code=400, detail="该任务不支持暂停")
    redis_conn = request.app.state.redis
    await set_paused(redis_conn, name, True)
    return {"ok": True, "name": name, "paused": True}


@router.post("/kline-tasks/{name}/resume")
async def admin_resume_kline_task(
    name: str,
    request: Request,
    _admin: User = Depends(get_current_admin),
) -> dict:
    """恢复一个 K 线修正任务"""
    from app.services.kline_task_status import KLINE_TASKS, set_paused

    meta = KLINE_TASKS.get(name)
    if meta is None:
        raise HTTPException(status_code=404, detail="未知任务")
    if not meta["pausable"]:
        raise HTTPException(status_code=400, detail="该任务不支持暂停")
    redis_conn = request.app.state.redis
    await set_paused(redis_conn, name, False)
    return {"ok": True, "name": name, "paused": False}


@router.post("/kline-tasks/{name}/run")
async def admin_run_kline_task(
    name: str,
    request: Request,
    _admin: User = Depends(get_current_admin),
) -> dict:
    """手动触发一次(在接收请求的 worker 上立即执行,幂等)"""
    import asyncio

    from app.services.kline_task_status import KLINE_TASKS

    meta = KLINE_TASKS.get(name)
    if meta is None:
        raise HTTPException(status_code=404, detail="未知任务")
    if not meta["runnable"]:
        raise HTTPException(status_code=400, detail="该任务不支持手动触发")
    redis_conn = request.app.state.redis
    from app.services import scheduler as sched

    sf = getattr(sched, "_session_factory", None)

    async def _job() -> None:
        try:
            if name == "kline_sync":
                from app.services.kline_sync import sync_kline_from_tickdata

                await sync_kline_from_tickdata(sf, redis_conn, trigger="manual")
            elif name == "kline_reconcile":
                from app.services.kline import reconcile_forming_with_vvtr

                await reconcile_forming_with_vvtr(redis_conn)
            elif name == "vvtr_tickdata_sync":
                from app.services.vvtr_tickdata_sync import sync_masters_to_tickdata

                await sync_masters_to_tickdata(redis_conn, days=2.0)
            elif name == "tickdata_gap_backfill":
                from app.services.tickdata_gap_backfill import backfill_gaps_once

                await backfill_gaps_once(redis_conn, sf, trigger="manual")
        except Exception:
            import logging

            logging.getLogger("qihuo.admin").exception(
                "手动触发 K 线任务失败 %s", name
            )

    asyncio.create_task(_job())
    return {"ok": True, "started": True, "name": name}


@router.get("/kline-tasks/{name}/logs")
async def admin_get_kline_task_logs(
    name: str,
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    _admin: User = Depends(get_current_admin),
) -> dict:
    """任务级日志(新在前,默认 100 条)"""
    from app.services.kline_task_status import KLINE_TASKS, read_logs

    if name not in KLINE_TASKS:
        raise HTTPException(status_code=404, detail="未知任务")
    redis_conn = getattr(request.app.state, "redis", None)
    logs = await read_logs(redis_conn, name, limit) if redis_conn else []
    return {"name": name, "logs": logs}


# ---------- 运维告警事件流 ----------


@router.get("/ops-alerts")
async def admin_get_ops_alerts(
    request: Request,
    _admin: User = Depends(get_current_admin),
) -> dict:
    """运维告警事件流（最近 200 条，最新在前）

    事件源：simnow_fallback / bridge_self_heal / channel_switch / bridge_down。
    数据来自 market:alert:log（Redis List，TTL 7 天）。
    """
    from app.services.ops_alert import read_ops_alerts

    redis_conn = getattr(request.app.state, "redis", None)
    alerts = await read_ops_alerts(redis_conn, limit=200)
    return {"alerts": alerts}


@router.get("/overview")
async def admin_overview(
    request: Request,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """管理仪表盘聚合：渠道 / 系统 / AI / 用户资金四块摘要"""
    from app.services.admin_overview import collect_admin_overview

    redis_conn = getattr(request.app.state, "redis", None)
    return await collect_admin_overview(session, redis_conn)


# ---------- 用户 CRUD ----------


@router.get("/users")
async def admin_list_users(
    keyword: str = Query(default=""),
    status: str = Query(default="all", description="all/active/frozen"),
    role: str = Query(default="all", description="all/user/admin"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """用户列表（分页搜索；权益摘要默认实盘）"""
    return await users_svc.list_users(
        session, keyword, status, role, limit, offset
    )


@router.post("/users", status_code=201)
async def admin_create_user(
    body: AdminUserCreateRequest,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """创建用户"""
    user = await users_svc.create_user(
        session,
        body.username,
        body.password,
        body.phone,
        body.email,
        body.role,
        body.status,
    )
    return users_svc.user_to_admin_dict(user)


@router.get("/users/{user_id}")
async def admin_get_user(
    user_id: UUID,
    trading_mode: str = Query(default="live", description="live/virtual"),
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """用户详情总览（按 trading_mode 分盘）"""
    return await user_inspect_svc.get_user_overview(
        session, user_id, _mode_q(trading_mode)
    )


@router.put("/users/{user_id}")
async def admin_update_user(
    user_id: UUID,
    body: AdminUserUpdateRequest,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """更新用户"""
    user = await users_svc.update_user(
        session,
        user_id,
        body.phone,
        body.email,
        body.role,
        body.status,
        body.password,
        body.clear_phone,
        body.clear_email,
        admin.id,
    )
    # per-user 配额覆盖（exclude_unset：未传=不改；传 null=清除回退全局；传值=覆盖）
    payload = body.model_dump(exclude_unset=True)
    if "max_tasks_override" in payload:
        val = payload["max_tasks_override"]
        user.max_tasks_override = None if val is None else max(0, int(val))
    if "factor_lab_daily_override" in payload:
        val = payload["factor_lab_daily_override"]
        user.factor_lab_daily_override = None if val is None else max(0, int(val))
    if "backtest_daily_override" in payload:
        val = payload["backtest_daily_override"]
        user.backtest_daily_override = None if val is None else max(0, int(val))
    await session.flush()
    return users_svc.user_to_admin_dict(user)


@router.get("/users/{user_id}/quota")
async def admin_user_quota(
    user_id: UUID,
    request: Request,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """用户配额：当前覆盖值 + 生效额度（None=不限）+ 当日已用次数"""
    from app.services.quota.quota_svc import (
        FEATURE_BACKTEST,
        FEATURE_FACTOR,
        count_active_tasks,
        daily_used,
        get_effective_limits,
    )

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    limits = await get_effective_limits(session, user)
    redis_conn = getattr(request.app.state, "redis", None)
    return {
        "max_tasks_override": getattr(user, "max_tasks_override", None),
        "factor_lab_daily_override": getattr(user, "factor_lab_daily_override", None),
        "backtest_daily_override": getattr(user, "backtest_daily_override", None),
        "effective": limits,
        "used": {
            "tasks": await count_active_tasks(session, user),
            "factor_today": await daily_used(redis_conn, user.id, FEATURE_FACTOR),
            "backtest_today": await daily_used(redis_conn, user.id, FEATURE_BACKTEST),
        },
    }


@router.delete("/users/{user_id}")
async def admin_delete_user(
    user_id: UUID,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """删除用户"""
    return await users_svc.delete_user(session, user_id, admin.id)


# ---------- 用户交易 / AI（分盘） ----------


@router.get("/users/{user_id}/orders")
async def admin_user_orders(
    user_id: UUID,
    status: str | None = Query(default=None),
    trading_mode: str = Query(default="live"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """查看用户模拟交易委托"""
    return await user_inspect_svc.list_user_orders(
        session, user_id, status, limit, offset, _mode_q(trading_mode)
    )


@router.get("/users/{user_id}/positions")
async def admin_user_positions(
    user_id: UUID,
    trading_mode: str = Query(default="live"),
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """查看用户持仓"""
    return await user_inspect_svc.list_user_positions(
        session, user_id, _mode_q(trading_mode)
    )


@router.get("/users/{user_id}/ai-tasks")
async def admin_user_ai_tasks(
    user_id: UUID,
    trading_mode: str = Query(default="live"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """查看用户 AI 交易任务（运行/持仓/盈亏）"""
    return await user_inspect_svc.list_user_ai_tasks(
        session, user_id, limit, offset, _mode_q(trading_mode)
    )


@router.get("/users/{user_id}/ai-decisions")
async def admin_user_ai_decisions(
    user_id: UUID,
    task_id: UUID | None = Query(default=None),
    trading_mode: str = Query(default="live"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """查看用户 AI 决策日志"""
    return await user_inspect_svc.list_user_ai_decisions(
        session, user_id, task_id, limit, offset, _mode_q(trading_mode)
    )


@router.get("/users/{user_id}/run-logs")
async def admin_user_run_logs(
    user_id: UUID,
    task_id: UUID | None = Query(default=None),
    level: str | None = Query(default=None),
    event: str | None = Query(default=None),
    trading_mode: str = Query(default="live"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """查看用户任务运行日志（信号/下单/异常事件流）"""
    return await user_inspect_svc.list_user_run_logs(
        session, user_id, task_id, level, event, limit, offset,
        _mode_q(trading_mode),
    )


@router.get("/users/{user_id}/run-logs/export")
async def admin_user_run_logs_export(
    user_id: UUID,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    task_id: UUID | None = Query(default=None),
    level: str | None = Query(default=None),
    event: str | None = Query(default=None),
    trading_mode: str = Query(default="live"),
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """导出用户任务运行日志（CSV 展平关键字段 / JSON 全量）"""
    from app.services.ai_trading.task.run_log_export import (
        fetch_export_rows,
        rows_to_csv,
        rows_to_json,
    )

    mode = _mode_q(trading_mode)
    rows = await fetch_export_rows(
        session, user_id=user_id, task_id=task_id, level=level,
        event=event, trading_mode=mode,
    )
    stamp = __import__("datetime").datetime.utcnow().strftime("%Y%m%d_%H%M")
    if format == "json":
        return StreamingResponse(
            iter([rows_to_json(rows)]),
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="run_logs_{user_id.hex[:8]}_{stamp}.json"'
                )
            },
        )
    return StreamingResponse(
        iter(["﻿" + rows_to_csv(rows)]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="run_logs_{user_id.hex[:8]}_{stamp}.csv"'
            )
        },
    )


@router.get("/users/{user_id}/pnl")
async def admin_user_pnl(
    user_id: UUID,
    trading_mode: str = Query(default="live"),
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """查看用户盈亏汇总"""
    return await user_inspect_svc.get_user_pnl_summary(
        session, user_id, _mode_q(trading_mode)
    )
