"""管理后台请求/响应 Schema"""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class AdminUserCreateRequest(BaseModel):
    """管理员创建用户"""

    username: Annotated[str, Field(min_length=3, max_length=50)]
    password: Annotated[str, Field(min_length=6, max_length=100)]
    phone: Annotated[str | None, Field(max_length=20)] = None
    email: Annotated[str | None, Field(max_length=100)] = None
    role: Annotated[str, Field(description="user 或 admin")] = "user"
    status: Annotated[str, Field(description="active 或 frozen")] = "active"

    @field_validator("username")
    @classmethod
    def username_ok(cls, value: str) -> str:
        if not value.replace("_", "").isalnum():
            raise ValueError("用户名只能包含字母、数字和下划线")
        return value

    @field_validator("role")
    @classmethod
    def role_ok(cls, value: str) -> str:
        if value not in ("user", "admin"):
            raise ValueError("role 必须为 user 或 admin")
        return value

    @field_validator("status")
    @classmethod
    def status_ok(cls, value: str) -> str:
        if value not in ("active", "frozen"):
            raise ValueError("status 必须为 active 或 frozen")
        return value


class AdminUserUpdateRequest(BaseModel):
    """管理员更新用户（部分字段）"""

    phone: Annotated[str | None, Field(max_length=20)] = None
    email: Annotated[str | None, Field(max_length=100)] = None
    role: Annotated[str | None, Field(description="user 或 admin")] = None
    status: Annotated[str | None, Field(description="active 或 frozen")] = None
    password: Annotated[str | None, Field(min_length=6, max_length=100)] = None
    clear_phone: bool = False
    clear_email: bool = False
    # per-user 配额覆盖；传值=覆盖，显式传 null=清除覆盖(回退全局默认)，不传=不改
    max_tasks_override: int | None = None
    factor_lab_daily_override: int | None = Field(default=None, deprecated=True, description="旧日配额历史字段，保留兼容；不影响无限使用策略")
    backtest_daily_override: int | None = Field(default=None, deprecated=True, description="旧日配额历史字段，保留兼容；不影响无限使用策略")

    @field_validator("role")
    @classmethod
    def role_ok(cls, value: str | None) -> str | None:
        if value is not None and value not in ("user", "admin"):
            raise ValueError("role 必须为 user 或 admin")
        return value

    @field_validator("status")
    @classmethod
    def status_ok(cls, value: str | None) -> str | None:
        if value is not None and value not in ("active", "frozen"):
            raise ValueError("status 必须为 active 或 frozen")
        return value


class SystemSettingsUpdateRequest(BaseModel):
    """更新系统设置"""

    registration_enabled: bool | None = None
    daily_register_limit: Annotated[int | None, Field(ge=0, le=100000)] = None
    register_reset_hour: Annotated[int | None, Field(ge=0, le=23)] = None
    paper_claim_amount: Annotated[float | None, Field(ge=0)] = None
    paper_claim_period: Annotated[str | None, Field(description="monthly 或 daily")] = None
    paper_claim_reset_hour: Annotated[int | None, Field(ge=0, le=23)] = None
    paper_total_claim_cap: Annotated[float | None, Field(ge=0)] = None
    system_name: Annotated[str | None, Field(max_length=100)] = None
    market_data_channel: Annotated[
        str | None,
        Field(
            description="sina / openctp_724 / openctp_sim / real_ctp / vvtr",
        ),
    ] = None
    backup_channel: Annotated[
        str | None,
        Field(description="备用渠道（主故障时切换，仅非 7×24 真实渠道）"),
    ] = None
    channel_auto_switch: bool | None = None
    kline_source_mode: bool | None = Field(
        default=None,
        description="[已弃用] 旧版模式开关，新代码用 kline_primary_source/backup",
        deprecated=True,
    )
    kline_source_order: str | None = Field(
        default=None,
        description="[已弃用] 旧版启用列表，逗号分隔（sina/tqsdk/eastmoney）",
        deprecated=True,
    )
    kline_primary_source: str | None = Field(
        default=None,
        description="K线历史源主渠道：sina/tqsdk/eastmoney（K线全部走主渠道）",
    )
    kline_backup_sources: str | None = Field(
        default=None,
        description="K线历史源备用渠道列表，逗号分隔（主渠道故障时按序降级）",
    )
    kline_sync_enabled: bool | None = Field(
        default=None,
        description="K线定时修正开关：true=自动修正，false=关闭",
    )
    max_tasks_per_user: Annotated[int | None, Field(ge=0, le=10000)] = None
    factor_lab_daily_limit: Annotated[int | None, Field(ge=0, le=10000, deprecated=True, description="兼容旧客户端，保存及生效均为0，不限制使用次数")] = None
    backtest_daily_limit: Annotated[int | None, Field(ge=0, le=10000, deprecated=True, description="兼容旧客户端，保存及生效均为0，不限制使用次数")] = None
    market_refresh_interval_sec: Annotated[
        int | None,
        Field(ge=1, le=10, description="交易时段行情/K线/WS 刷新间隔默认秒数（1-10）"),
    ] = None
    market_refresh_intervals: dict[str, int] | None = Field(
        default=None,
        description='per-channel 刷新间隔 {channel: seconds}，每值 1-10；未列渠道用默认秒数',
    )
    # ----- VVTR（长尾猴量化）数据源 -----
    vvtr_enabled: bool | None = Field(
        default=None,
        description="VVTR 数据源总开关（false=桥休眠，渠道回落 sina）",
    )
    vvtr_api_key: str | None = Field(
        default=None,
        max_length=256,
        description="VVTR apiKey 明文（仅写入时提交；留空=不修改，接口只回显脱敏值）",
    )
    vvtr_api_key_clear: bool | None = Field(
        default=None,
        description="显式清除已存储的 apiKey",
    )
    vvtr_mobile: str | None = Field(
        default=None,
        max_length=32,
        description="VVTR 账号手机号（/my/permissions 权限查询用）",
    )
    vvtr_base_url: str | None = Field(
        default=None,
        max_length=128,
        description="VVTR REST 基础URL（默认 https://rest.vvtr.com/v1）",
    )
    vvtr_ws_url: str | None = Field(
        default=None,
        max_length=128,
        description="VVTR 深度行情 WS 地址（默认 wss://rest.vvtr.com/v2/connect）",
    )
    vvtr_quotes_enabled: bool | None = Field(
        default=None,
        description="VVTR 实时行情开关（报价/盘口/成交，WS v2 250~500ms）",
    )
    vvtr_ws_enabled: bool | None = Field(
        default=None,
        description="VVTR WSS 推送开关：开=尝试WS自动降级HTTP，关=仅HTTP快照轮询",
    )
    vvtr_kline_enabled: bool | None = Field(
        default=None,
        description="VVTR 历史K线源开关（注册进 kline 可选源）",
    )
    # ----- CTP 中转模拟行情（ctp_sim） -----
    ctp_sim_enabled: bool | None = Field(
        default=None,
        description="自定义 CTP 行情渠道开关（休市联调用）",
    )
    ctp_sim_md_front: str | None = Field(
        default=None,
        max_length=128,
        description="CTP 行情前置（tcp://host:port，用户自行填写）",
    )
    ctp_sim_broker_id: str | None = Field(
        default=None,
        max_length=16,
        description="CTP 中转模拟 BrokerID（默认 9999）",
    )
    ctp_sim_investor_id: str | None = Field(
        default=None,
        max_length=32,
        description="CTP 中转模拟账号（任意值可登录，默认 qihuo_sim）",
    )
    ctp_sim_password: str | None = Field(
        default=None,
        max_length=128,
        description="CTP 中转模拟密码（任意值可登录；留空=不修改，接口只回显脱敏值）",
    )
    ctp_sim_password_clear: bool | None = Field(
        default=None,
        description="显式清除已存储的 ctp_sim 密码",
    )
    @field_validator("ctp_sim_md_front")
    @classmethod
    def ctp_sim_front_ok(cls, value: str | None) -> str | None:
        if value is None:
            return value
        v = value.strip()
        if v and not v.startswith("tcp://"):
            raise ValueError("ctp_sim_md_front 必须形如 tcp://host:port")
        return v or None

    @field_validator("paper_claim_period")
    @classmethod
    def period_ok(cls, value: str | None) -> str | None:
        if value is not None and value not in ("monthly", "daily"):
            raise ValueError("paper_claim_period 必须为 monthly 或 daily")
        return value

    @field_validator("market_data_channel")
    @classmethod
    def channel_ok(cls, value: str | None) -> str | None:
        allowed = (
            "sina",
            "openctp_724",
            "openctp_sim",
            "real_ctp",
            "vvtr",
            "ctp_sim",
        )
        if value is not None and value not in allowed:
            raise ValueError(
                "market_data_channel 必须为 sina/"
                "openctp_724/openctp_sim/real_ctp/vvtr/ctp_sim"
            )
        return value

    @field_validator("backup_channel")
    @classmethod
    def backup_channel_ok(cls, value: str | None) -> str | None:
        # 备用渠道限定非 7×24 真实数据渠道
        allowed = ("sina", "openctp_sim", "real_ctp", "vvtr")
        if value is not None and value not in allowed:
            raise ValueError(
                "backup_channel 必须为 sina/openctp_sim/real_ctp/vvtr（非 7×24）"
            )
        return value

    @field_validator("market_refresh_intervals")
    @classmethod
    def refresh_intervals_ok(
        cls, value: dict[str, int] | None
    ) -> dict[str, int] | None:
        if value is None:
            return value
        from app.services.market_channel import REALTIME_CHANNELS

        cleaned: dict[str, int] = {}
        for k, v in value.items():
            key = str(k).strip().lower()
            if key not in REALTIME_CHANNELS:
                raise ValueError(
                    f"market_refresh_intervals 渠道 {key} 非法，"
                    f"必须为 sina/real_ctp/openctp_sim"
                )
            try:
                sec = int(v)
            except (TypeError, ValueError):
                raise ValueError(
                    f"market_refresh_intervals[{key}] 必须为整数"
                )
            if sec < 1 or sec > 10:
                raise ValueError(
                    f"market_refresh_intervals[{key}] 必须在 1-10 秒"
                )
            cleaned[key] = sec
        return cleaned

    @field_validator("kline_primary_source")
    @classmethod
    def kline_primary_ok(cls, value: str | None) -> str | None:
        allowed = ("sina", "tqsdk", "eastmoney", "simnow", "vvtr")
        if value is not None:
            v = value.strip().lower()
            if v not in allowed:
                raise ValueError(
                    "kline_primary_source 必须是 sina/tqsdk/eastmoney/simnow/vvtr"
                )
            return v
        return value

    @field_validator("kline_backup_sources")
    @classmethod
    def kline_backup_ok(cls, value: str | None) -> str | None:
        allowed = ("sina", "tqsdk", "eastmoney", "vvtr")
        if value is not None:
            parts = [s.strip().lower() for s in value.split(",") if s.strip()]
            # 过滤非法 + 去重保序
            parts = [p for p in parts if p in allowed]
            seen: set[str] = set()
            deduped: list[str] = []
            for p in parts:
                if p not in seen:
                    seen.add(p)
                    deduped.append(p)
            if not deduped:
                raise ValueError(
                    "kline_backup_sources 至少配置 1 个备用渠道（sina/tqsdk/eastmoney）"
                )
            return ",".join(deduped)
        return value

    @field_validator("kline_source_order")
    @classmethod
    def kline_sources_ok(cls, value: str | None) -> str | None:
        allowed = ("sina", "tqsdk", "eastmoney")
        if value is not None:
            parts = [s.strip().lower() for s in value.split(",") if s.strip()]
            if not parts or any(p not in allowed for p in parts):
                raise ValueError(
                    "kline_source_order 必须是 sina/tqsdk/eastmoney 逗号分隔列表"
                )
            if len(parts) < 2:
                raise ValueError("K 线数据源至少启用 2 个")
        return value

    @model_validator(mode="after")
    def kline_primary_not_in_backup(self) -> "SystemSettingsUpdateRequest":
        """主渠道不能同时出现在备用渠道列表中"""
        primary = self.kline_primary_source
        backup = self.kline_backup_sources
        if primary and backup:
            backups = [s.strip().lower() for s in backup.split(",") if s.strip()]
            if primary in backups:
                raise ValueError(
                    f"主渠道 {primary} 不能同时作为备用渠道"
                )
        return self


class AdminUserIdPath(BaseModel):
    """路径参数辅助（文档用）"""

    user_id: UUID
