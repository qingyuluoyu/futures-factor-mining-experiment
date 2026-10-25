"""K 线历史源运行时配置 —— 主渠道 + 备用渠道（Redis 镜像 + DB 兜底）

管理后台（PUT /api/admin/settings）保存 system_settings.kline_primary_source /
kline_backup_sources 后，同步写 Redis 镜像键 kline:source:config；
运行时 get_kline_history 热路径优先读 Redis，miss 回 DB（session 可用时），
再兜底代码默认（主=新浪，备=simnow/PG；tqsdk/eastmoney 已移除）。

返回 (primary, backups)：primary 为单源字符串；backups 为备用源列表。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("qihuo.kline_source_cfg")

CONFIG_KEY = "kline:source:config"
_CONFIG_TTL = 300

# 默认主渠道（东财 push2his 实测最稳）
DEFAULT_PRIMARY = "sina"
# 默认备用渠道（新浪；tqsdk 已移除）
DEFAULT_BACKUP = "sina"


async def _write_redis(
    redis_conn: aioredis.Redis,
    primary: str,
    backups: list[str],
) -> None:
    try:
        await redis_conn.set(
            CONFIG_KEY,
            json.dumps({"primary": primary, "backups": backups}),
            ex=_CONFIG_TTL,
        )
    except Exception:
        logger.debug("写 kline:source:config 失败", exc_info=True)


def _migrate_legacy(
    primary: str | None,
    backups: list[str] | None,
    legacy_order: str | None,
    legacy_mode: bool | None,
) -> tuple[str, list[str]]:
    """从旧版 mode/order 结构回填新结构（仅在新字段为空时）

    旧 kline_source_order 逗号串：第 1 个作主渠道，其余作备用。
    """
    p = (primary or "").strip().lower()
    b = list(backups or [])
    if not p and not b and legacy_order:
        parts = [s.strip().lower() for s in legacy_order.split(",") if s.strip()]
        if parts:
            p = parts[0]
            b = parts[1:]
    if not p:
        p = DEFAULT_PRIMARY
    if not b:
        b = DEFAULT_BACKUP.split(",")
    return p, b


async def get_kline_source_config(
    redis_conn: aioredis.Redis | None,
    session: Any = None,
) -> tuple[str, list[str]]:
    """返回 (primary, backups)。

    primary: 主渠道单源（如 "sina"）；
    backups: 备用源列表（如 ["sina"]）。
    顺序：Redis 镜像 → DB（并回写 Redis）→ 代码默认。

    兼容旧结构：若 Redis/DB 中存的是旧版 {mode, order}，
    自动回填为 (primary, backups) 并以新结构回写。
    """
    # ---- Redis 镜像 ----
    if redis_conn is not None:
        try:
            raw = await redis_conn.get(CONFIG_KEY)
            if raw:
                payload = json.loads(raw)
                if "primary" in payload:
                    primary = str(payload.get("primary") or DEFAULT_PRIMARY)
                    backups_raw = payload.get("backups") or []
                    backups = [str(s) for s in backups_raw if s]
                    return primary, backups
                # 旧结构 {mode, order} → 回填
                if "order" in payload:
                    primary, backups = _migrate_legacy(
                        None, None, str(payload.get("order") or ""), None
                    )
                    await _write_redis(redis_conn, primary, backups)
                    return primary, backups
        except Exception:
            pass

    # ---- DB 兜底 ----
    if session is not None:
        try:
            from app.services.system_settings_svc import (
                ensure_system_settings,
                settings_to_dict,
            )

            row = await ensure_system_settings(session)
            cfg = settings_to_dict(row)
            primary = str(cfg.get("kline_primary_source") or "").strip().lower()
            backups_raw = str(cfg.get("kline_backup_sources") or "")
            backups = [s.strip().lower() for s in backups_raw.split(",") if s.strip()]
            # 新列为空 → 从旧 kline_source_order 回填
            if not primary and not backups:
                primary, backups = _migrate_legacy(
                    None,
                    None,
                    str(cfg.get("kline_source_order") or ""),
                    cfg.get("kline_source_mode"),
                )
            if not primary:
                primary = DEFAULT_PRIMARY
            if not backups:
                backups = DEFAULT_BACKUP.split(",")
            if redis_conn is not None:
                await _write_redis(redis_conn, primary, backups)
            return primary, backups
        except Exception:
            logger.debug("读 system_settings K 线源配置失败", exc_info=True)

    return DEFAULT_PRIMARY, DEFAULT_BACKUP.split(",")


async def write_kline_source_config_to_redis(
    redis_conn: aioredis.Redis | None,
    cfg: dict[str, Any],
) -> None:
    """admin PUT 保存后同步镜像（cfg 为 settings_to_dict 输出）"""
    if redis_conn is None:
        return
    primary = str(cfg.get("kline_primary_source") or "").strip().lower()
    backups_raw = str(cfg.get("kline_backup_sources") or "")
    backups = [s.strip().lower() for s in backups_raw.split(",") if s.strip()]
    # 新字段为空时从旧 order 回填（兼容尚未迁移的写入）
    if not primary and not backups:
        primary, backups = _migrate_legacy(
            None, None, str(cfg.get("kline_source_order") or ""), None
        )
    if not primary:
        primary = DEFAULT_PRIMARY
    if not backups:
        backups = DEFAULT_BACKUP.split(",")
    await _write_redis(redis_conn, primary, backups)
