"""系统设置服务 —— 读写全局配置、注册窗口与领取周期键"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_settings import SystemSettings

SETTINGS_TZ = ZoneInfo("Asia/Shanghai")
SINGLETON_ID = 1

# K 线源默认（主渠道 + 备用渠道）
DEFAULT_KLINE_PRIMARY = "eastmoney"
DEFAULT_KLINE_BACKUP = "sina,tqsdk"
# [已弃用] 旧版启用列表默认值（保留兼容）
DEFAULT_KLINE_SOURCE_ORDER = "sina,tqsdk,eastmoney"
VALID_KLINE_SOURCES = ("sina", "tqsdk", "eastmoney", "simnow", "vvtr")


def _parse_refresh_map(raw: object) -> dict[str, int]:
    """把 market_refresh_intervals 列值（JSON 字符串）解析为 {channel: seconds}"""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, dict):
            return {str(k): int(v) for k, v in data.items()}
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return {}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _decrypt_vvtr_key(raw: object) -> str:
    """解密存储的秘密列（AES-GCM）；空/损坏返回空串（不抛错）"""
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        from app.services.crypto import decrypt

        return decrypt(text)
    except Exception:
        return ""


def _mask_vvtr_key(raw: object) -> str:
    """脱敏展示：仅当能解出明文时给前4后4"""
    plain = _decrypt_vvtr_key(raw)
    if not plain:
        return ""
    from app.services.crypto import mask_key

    return mask_key(plain)


def _clamp_hour(hour: int) -> int:
    """将小时钳制到 0-23"""
    if hour < 0:
        return 0
    if hour > 23:
        return 23
    return hour


async def ensure_system_settings(session: AsyncSession) -> SystemSettings:
    """获取或创建默认系统设置（单例 id=1）"""
    stmt = select(SystemSettings).where(SystemSettings.id == SINGLETON_ID)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        return row

    row = SystemSettings(
        id=SINGLETON_ID,
        registration_enabled=True,
        daily_register_limit=100,
        register_reset_hour=6,
        paper_claim_amount=Decimal("1000000.00"),
        paper_claim_period="monthly",
        paper_claim_reset_hour=6,
        paper_total_claim_cap=Decimal("0"),
        system_name="期货交易模拟系统",
        market_data_channel="sina",
        backup_channel="sina",
        channel_auto_switch=True,
        kline_source_mode=False,
        kline_source_order=DEFAULT_KLINE_SOURCE_ORDER,
        updated_at=_now_utc(),
    )
    session.add(row)
    await session.flush()
    return row


def settings_to_dict(row: SystemSettings) -> dict:
    """系统设置转响应 dict"""
    return {
        "registration_enabled": bool(row.registration_enabled),
        "daily_register_limit": int(row.daily_register_limit),
        "register_reset_hour": int(row.register_reset_hour),
        "paper_claim_amount": float(row.paper_claim_amount),
        "paper_claim_period": row.paper_claim_period,
        "paper_claim_reset_hour": int(row.paper_claim_reset_hour),
        "paper_total_claim_cap": float(row.paper_total_claim_cap),
        "system_name": row.system_name,
        "market_data_channel": str(
            getattr(row, "market_data_channel", None) or "sina"
        ),
        "backup_channel": str(getattr(row, "backup_channel", None) or "sina"),
        "channel_auto_switch": bool(
            getattr(row, "channel_auto_switch", True)
        ),
        "kline_source_mode": bool(
            getattr(row, "kline_source_mode", False)
        ),
        "kline_source_order": str(
            getattr(row, "kline_source_order", None) or DEFAULT_KLINE_SOURCE_ORDER
        ),
        "kline_primary_source": str(
            getattr(row, "kline_primary_source", None) or DEFAULT_KLINE_PRIMARY
        ),
        "kline_backup_sources": str(
            getattr(row, "kline_backup_sources", None) or DEFAULT_KLINE_BACKUP
        ),
        "kline_sync_enabled": bool(
            getattr(row, "kline_sync_enabled", True)
        ),
        "max_tasks_per_user": int(getattr(row, "max_tasks_per_user", 5)),
        "factor_lab_daily_limit": 0,
        "backtest_daily_limit": 0,
        "market_refresh_interval_sec": int(
            getattr(row, "market_refresh_interval_sec", 1)
        ),
        "market_refresh_intervals": _parse_refresh_map(
            getattr(row, "market_refresh_intervals", "{}")
        ),
        "vvtr_enabled": bool(getattr(row, "vvtr_enabled", False)),
        # apiKey 只回显脱敏值，明文永不出接口
        "vvtr_api_key_masked": _mask_vvtr_key(getattr(row, "vvtr_api_key", "")),
        "vvtr_api_key_set": bool(_decrypt_vvtr_key(getattr(row, "vvtr_api_key", ""))),
        "vvtr_mobile": str(getattr(row, "vvtr_mobile", "") or ""),
        "vvtr_base_url": str(
            getattr(row, "vvtr_base_url", "") or "https://rest.vvtr.com/v1"
        ),
        "vvtr_ws_url": str(
            getattr(row, "vvtr_ws_url", "") or "wss://rest.vvtr.com/v2/connect"
        ),
        "vvtr_quotes_enabled": bool(getattr(row, "vvtr_quotes_enabled", True)),
        "vvtr_ws_enabled": bool(getattr(row, "vvtr_ws_enabled", True)),
        "vvtr_kline_enabled": bool(getattr(row, "vvtr_kline_enabled", False)),
        # ----- CTP 中转模拟行情（ctp_sim） -----
        "ctp_sim_enabled": bool(getattr(row, "ctp_sim_enabled", False)),
        "ctp_sim_md_front": str(getattr(row, "ctp_sim_md_front", "") or ""),
        "ctp_sim_broker_id": str(getattr(row, "ctp_sim_broker_id", "") or "9999"),
        "ctp_sim_investor_id": str(
            getattr(row, "ctp_sim_investor_id", "") or "qihuo_sim"
        ),
        # 密码只回显脱敏值，明文永不出接口
        "ctp_sim_password_masked": _mask_vvtr_key(
            getattr(row, "ctp_sim_password", "")
        ),
        "ctp_sim_password_set": bool(
            _decrypt_vvtr_key(getattr(row, "ctp_sim_password", ""))
        ),
        "updated_at": (
            row.updated_at.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
            if row.updated_at and row.updated_at.tzinfo is None
            else (
                row.updated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                if row.updated_at
                else None
            )
        ),
    }


def current_register_window_start(
    reset_hour: int,
    now: datetime | None = None,
) -> datetime:
    """当前注册计数窗口起点（上海时区 aware）"""
    hour = _clamp_hour(reset_hour)
    local = (now or datetime.now(SETTINGS_TZ)).astimezone(SETTINGS_TZ)
    candidate = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if local < candidate:
        candidate = candidate - timedelta(days=1)
    return candidate


def current_claim_period_key(
    period: str,
    reset_hour: int,
    now: datetime | None = None,
) -> str:
    """当前领取周期键：monthly→YYYY-MM；daily→YYYY-MM-DD（按重置小时）"""
    hour = _clamp_hour(reset_hour)
    local = (now or datetime.now(SETTINGS_TZ)).astimezone(SETTINGS_TZ)
    if period == "daily":
        boundary = local.replace(hour=hour, minute=0, second=0, microsecond=0)
        if local < boundary:
            day = (local - timedelta(days=1)).date()
        else:
            day = local.date()
        return day.isoformat()
    # 默认 monthly：自然月（忽略 reset_hour，与历史 last_claim_month 兼容）
    return f"{local.year:04d}-{local.month:02d}"


async def update_system_settings(
    session: AsyncSession,
    payload: dict,
) -> SystemSettings:
    """按字段更新系统设置（仅接受已知键）"""
    row = await ensure_system_settings(session)

    if "registration_enabled" in payload and payload["registration_enabled"] is not None:
        row.registration_enabled = bool(payload["registration_enabled"])
    if "daily_register_limit" in payload and payload["daily_register_limit"] is not None:
        limit = int(payload["daily_register_limit"])
        row.daily_register_limit = max(0, limit)
    if "register_reset_hour" in payload and payload["register_reset_hour"] is not None:
        row.register_reset_hour = _clamp_hour(int(payload["register_reset_hour"]))
    if "paper_claim_amount" in payload and payload["paper_claim_amount"] is not None:
        amount = Decimal(str(payload["paper_claim_amount"]))
        if amount < 0:
            amount = Decimal("0")
        row.paper_claim_amount = amount
    if "paper_claim_period" in payload and payload["paper_claim_period"] is not None:
        period = str(payload["paper_claim_period"])
        if period not in ("monthly", "daily"):
            period = "monthly"
        row.paper_claim_period = period
    if "paper_claim_reset_hour" in payload and payload["paper_claim_reset_hour"] is not None:
        row.paper_claim_reset_hour = _clamp_hour(int(payload["paper_claim_reset_hour"]))
    if "paper_total_claim_cap" in payload and payload["paper_total_claim_cap"] is not None:
        cap = Decimal(str(payload["paper_total_claim_cap"]))
        if cap < 0:
            cap = Decimal("0")
        row.paper_total_claim_cap = cap
    if "system_name" in payload and payload["system_name"] is not None:
        name = str(payload["system_name"]).strip()
        row.system_name = name[:100] if name else row.system_name
    if "market_data_channel" in payload and payload["market_data_channel"] is not None:
        channel = str(payload["market_data_channel"]).strip().lower()
        if channel not in (
            "sina",
            "openctp_724",
            "openctp_sim",
            "real_ctp",
            "vvtr",
            "ctp_sim",
        ):
            channel = "sina"
        row.market_data_channel = channel
    if "backup_channel" in payload and payload["backup_channel"] is not None:
        backup = str(payload["backup_channel"]).strip().lower()
        if backup not in ("sina", "openctp_sim", "real_ctp", "vvtr"):
            backup = "sina"
        row.backup_channel = backup
    if (
        "channel_auto_switch" in payload
        and payload["channel_auto_switch"] is not None
    ):
        row.channel_auto_switch = bool(payload["channel_auto_switch"])
    # K 线历史源：主渠道 + 备用渠道（新模型）
    if (
        "kline_primary_source" in payload
        and payload["kline_primary_source"] is not None
    ):
        primary = str(payload["kline_primary_source"]).strip().lower()
        if primary not in VALID_KLINE_SOURCES:
            primary = DEFAULT_KLINE_PRIMARY
        row.kline_primary_source = primary
    if (
        "kline_backup_sources" in payload
        and payload["kline_backup_sources"] is not None
    ):
        backups = [
            s.strip().lower()
            for s in str(payload["kline_backup_sources"]).split(",")
            if s.strip().lower() in VALID_KLINE_SOURCES
        ]
        # 去重保序，并移除与主渠道重复的项
        primary_now = (
            str(payload.get("kline_primary_source") or getattr(row, "kline_primary_source", ""))
            .strip().lower()
        )
        seen: set[str] = set()
        deduped: list[str] = []
        for s in backups:
            if s == primary_now:
                continue
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        row.kline_backup_sources = ",".join(deduped) or DEFAULT_KLINE_BACKUP
    if (
        "kline_sync_enabled" in payload
        and payload["kline_sync_enabled"] is not None
    ):
        row.kline_sync_enabled = bool(payload["kline_sync_enabled"])
    # [已弃用] 旧字段兼容写入（前端旧版本仍可能发送）
    if (
        "kline_source_mode" in payload
        and payload["kline_source_mode"] is not None
    ):
        row.kline_source_mode = bool(payload["kline_source_mode"])
    if (
        "kline_source_order" in payload
        and payload["kline_source_order"] is not None
    ):
        order = ",".join(
            s.strip().lower()
            for s in str(payload["kline_source_order"]).split(",")
            if s.strip().lower() in VALID_KLINE_SOURCES
        )
        row.kline_source_order = order or DEFAULT_KLINE_SOURCE_ORDER
    if (
        "max_tasks_per_user" in payload
        and payload["max_tasks_per_user"] is not None
    ):
        row.max_tasks_per_user = max(0, int(payload["max_tasks_per_user"]))
    if (
        "factor_lab_daily_limit" in payload
        and payload["factor_lab_daily_limit"] is not None
    ):
        row.factor_lab_daily_limit = 0
    if (
        "backtest_daily_limit" in payload
        and payload["backtest_daily_limit"] is not None
    ):
        row.backtest_daily_limit = 0
    if (
        "market_refresh_interval_sec" in payload
        and payload["market_refresh_interval_sec"] is not None
    ):
        # 双保险钳位（schema 层已 ge=1 le=10），防止绕过 API 直接调 svc
        row.market_refresh_interval_sec = max(
            1, min(10, int(payload["market_refresh_interval_sec"]))
        )
    if (
        "market_refresh_intervals" in payload
        and payload["market_refresh_intervals"] is not None
    ):
        raw_map = payload["market_refresh_intervals"]
        # schema 层已校验 key∈REALTIME_CHANNELS、value 1-10；此处落库为 JSON 字符串
        row.market_refresh_intervals = (
            json.dumps(raw_map, ensure_ascii=False)
            if isinstance(raw_map, dict)
            else "{}"
        )
    # ----- VVTR 数据源 -----
    if "vvtr_enabled" in payload and payload["vvtr_enabled"] is not None:
        row.vvtr_enabled = bool(payload["vvtr_enabled"])
    if "vvtr_quotes_enabled" in payload and payload["vvtr_quotes_enabled"] is not None:
        row.vvtr_quotes_enabled = bool(payload["vvtr_quotes_enabled"])
    if "vvtr_ws_enabled" in payload and payload["vvtr_ws_enabled"] is not None:
        row.vvtr_ws_enabled = bool(payload["vvtr_ws_enabled"])
    if "vvtr_kline_enabled" in payload and payload["vvtr_kline_enabled"] is not None:
        row.vvtr_kline_enabled = bool(payload["vvtr_kline_enabled"])
    if "vvtr_mobile" in payload:
        row.vvtr_mobile = str(payload["vvtr_mobile"] or "").strip()[:32]
    if "vvtr_base_url" in payload and payload["vvtr_base_url"] is not None:
        base = str(payload["vvtr_base_url"]).strip()[:128]
        row.vvtr_base_url = base or "https://rest.vvtr.com/v1"
    if "vvtr_ws_url" in payload and payload["vvtr_ws_url"] is not None:
        ws = str(payload["vvtr_ws_url"]).strip()[:128]
        row.vvtr_ws_url = ws or "wss://rest.vvtr.com/v2/connect"
    # apiKey：非空明文 → 加密落库；空串/None → 保持不变（前端留空=不修改）
    if "vvtr_api_key" in payload:
        plain = str(payload["vvtr_api_key"] or "").strip()
        if plain:
            from app.services.crypto import encrypt

            row.vvtr_api_key = encrypt(plain)[:512]
    # 显式清除密钥
    if payload.get("vvtr_api_key_clear"):
        row.vvtr_api_key = ""
    # ----- CTP 中转模拟行情（ctp_sim） -----
    if "ctp_sim_enabled" in payload and payload["ctp_sim_enabled"] is not None:
        row.ctp_sim_enabled = bool(payload["ctp_sim_enabled"])
    if "ctp_sim_md_front" in payload and payload["ctp_sim_md_front"] is not None:
        front = str(payload["ctp_sim_md_front"]).strip()[:128]
        row.ctp_sim_md_front = front
    if "ctp_sim_broker_id" in payload and payload["ctp_sim_broker_id"] is not None:
        broker = str(payload["ctp_sim_broker_id"]).strip()[:16]
        row.ctp_sim_broker_id = broker or "9999"
    if "ctp_sim_investor_id" in payload and payload["ctp_sim_investor_id"] is not None:
        investor = str(payload["ctp_sim_investor_id"]).strip()[:32]
        row.ctp_sim_investor_id = investor or "qihuo_sim"
    # 密码：非空明文 → 加密落库；空串/None → 保持不变（前端留空=不修改）
    if "ctp_sim_password" in payload:
        plain = str(payload["ctp_sim_password"] or "").strip()
        if plain:
            from app.services.crypto import encrypt

            row.ctp_sim_password = encrypt(plain)[:512]
    if payload.get("ctp_sim_password_clear"):
        row.ctp_sim_password = ""
    row.updated_at = _now_utc()
    await session.flush()
    return row
