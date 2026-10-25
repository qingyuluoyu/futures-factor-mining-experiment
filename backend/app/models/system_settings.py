"""系统全局配置 —— 注册策略与模拟资金规则（单行表）"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SystemSettings(Base):
    """系统设置表 —— 全局仅一行（id 固定为 1）"""

    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        default=1,
        comment="固定主键 1",
    )
    # ----- 注册策略 -----
    registration_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="是否开放注册",
    )
    daily_register_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        comment="每日注册上限，0 表示不限制",
    )
    register_reset_hour: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=6,
        comment="注册计数重置小时（上海时区 0-23，默认 6）",
    )
    # ----- 模拟资金 -----
    paper_claim_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("1000000.00"),
        comment="单次可领取练手资金",
    )
    # monthly=自然月；daily=按重置小时划分的自然日
    paper_claim_period: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="monthly",
        comment="领取周期 monthly/daily",
    )
    paper_claim_reset_hour: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=6,
        comment="领取周期重置小时（上海时区，默认 6）",
    )
    # 0 表示不限制累计领取总额
    paper_total_claim_cap: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        comment="可领取练手资金总额上限，0 为不限",
    )
    # 系统展示名（可选）
    system_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="期货交易模拟系统",
        comment="系统名称",
    )
    # 实时行情渠道：sina | openctp | real_ctp | vvtr（历史 K 线仍固定新浪；SimNow 已下线）
    market_data_channel: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="sina",
        comment="实时行情渠道 sina/openctp/real_ctp/vvtr",
    )
    # 备用行情渠道：主渠道故障时自动切换（仅非 7×24 真实渠道）
    backup_channel: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="sina",
        comment="备用行情渠道 sina/openctp_sim/real_ctp/vvtr",
    )
    # 是否启用主备自动切换（主渠道恢复后自动回切）
    channel_auto_switch: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="是否启用主备渠道自动切换",
    )
    # ----- K 线历史数据源（主渠道 + 备用渠道） -----
    # [已弃用] 旧版轮询开关：false=主备，true=轮询。保留列兼容旧数据，新代码不读写。
    kline_source_mode: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="[已弃用] K线历史源模式：false=主备，true=轮询",
    )
    # [已弃用] 旧版启用列表。保留列兼容旧数据，新代码不读写。
    kline_source_order: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="sina,tqsdk,eastmoney",
        comment="[已弃用] K线历史源顺序/启用列表，逗号分隔",
    )
    # 主渠道：K 线全部优先走此源（sina/tqsdk/eastmoney）
    kline_primary_source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="eastmoney",
        comment="K线历史源主渠道（单源），失败自动切换备用渠道",
    )
    # 备用渠道：主渠道失败时按此顺序降级（逗号分隔）
    kline_backup_sources: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="sina,tqsdk",
        comment="K线历史源备用渠道列表，逗号分隔",
    )
    # K线定时修正开关（true=每天02:00+15:30自动修正，false=关闭）
    kline_sync_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="K线定时修正开关：true=自动修正，false=关闭",
    )
    # 交易时段行情拉取/K线 forming/WS 推送的统一刷新间隔（秒）
    # 1=每秒（默认，最实时）；改大后行情与 K 线刷新同步降频
    market_refresh_interval_sec: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
        server_default="5",
        comment="交易时段行情/K线/WS 刷新间隔默认秒数（1-10），未单独配置的渠道用此值",
    )
    # per-channel 刷新间隔覆盖（JSON），如 {"sina":3}；未列出的渠道用 market_refresh_interval_sec
    market_refresh_intervals: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        default="{}",
        server_default="{}",
        comment='per-channel 刷新间隔 JSON，如 {"sina":3}，覆盖默认秒数',
    )
    # ----- VVTR（长尾猴量化）数据源 -----
    # 总开关：false 时 WS 桥休眠、渠道回落 sina（购买服务前保持 false）
    vvtr_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="VVTR 数据源总开关",
    )
    # apiKey（AES-256-GCM 加密存储，接口仅回显脱敏值）
    vvtr_api_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        default="",
        server_default="",
        comment="VVTR apiKey（加密存储）",
    )
    # 账号手机号（/my/permissions 权限查询需要）
    vvtr_mobile: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="",
        server_default="",
        comment="VVTR 账号手机号（权限查询用）",
    )
    vvtr_base_url: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="https://rest.vvtr.com/v1",
        server_default="https://rest.vvtr.com/v1",
        comment="VVTR REST 基础URL",
    )
    vvtr_ws_url: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="wss://rest.vvtr.com/v2/connect",
        server_default="wss://rest.vvtr.com/v2/connect",
        comment="VVTR 深度行情 WS 地址",
    )
    # 接口级开关：实时行情（报价/盘口/成交，WS v2 250~500ms 推送）
    vvtr_quotes_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="VVTR 实时行情开关（报价/盘口/成交）",
    )
    # 接口级开关：WSS 推送通道（v2 深度 / v1 L1；关闭则纯 HTTP 快照轮询）
    vvtr_ws_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="VVTR WSS 推送开关：开=尝试WS(自动降级HTTP)，关=仅HTTP快照",
    )
    # 接口级开关：历史K线源（注册进 kline_provider 可选源）
    vvtr_kline_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="VVTR 历史K线源开关",
    )
    # ----- CTP 中转模拟行情（ctp_sim：7×24，任意账号，仅 Tick/K线实时联调） -----
    # 总开关：false 时管理端不可选 ctp_sim 渠道（回落 sina）
    ctp_sim_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="CTP 中转模拟行情开关",
    )
    ctp_sim_md_front: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="",
        server_default="",
        comment="CTP 行情前置地址（用户自行填写 tcp://host:port）",
    )
    ctp_sim_broker_id: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="9999",
        server_default="9999",
        comment="CTP 中转模拟 BrokerID",
    )
    ctp_sim_investor_id: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="qihuo_sim",
        server_default="qihuo_sim",
        comment="CTP 中转模拟账号（任意值可登录）",
    )
    # 密码（AES-256-GCM 加密存储，接口仅回显脱敏值；任意值可登录）
    ctp_sim_password: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        default="",
        server_default="",
        comment="CTP 中转模拟密码（加密存储）",
    )
    # ----- 用量配额全局默认（0=不限；per-user override 覆盖此处）-----
    max_tasks_per_user: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
        server_default="5",
        comment="每用户最大任务数(running+paused)，0=不限",
    )
    factor_lab_daily_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default="3",
        comment="因子实验室每日调用次数，0=不限",
    )
    backtest_daily_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=2,
        server_default="2",
        comment="历史回测每日调用次数，0=不限",
    )
    # ----- 因子 token 编码版本 -----
    # 1 = 算子 id 起点 14（随特征数漂移的旧编码）
    # 2 = 算子 id 起点 64（特征 id 空间冻结，见 factor_lab/token_encoding.py）
    # 低于当前版本时，存量 token 需先由
    # scripts/migrate-factor-token-encoding.py 迁移，否则拒绝执行
    factor_token_encoding_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        comment="因子 token 编码版本，1=旧编码待迁移，2=特征空间已冻结",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="更新时间",
    )
