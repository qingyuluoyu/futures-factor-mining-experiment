"""AI 交易请求/响应 schema"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

from app.schemas.instrument_scope import CnyFuturesSymbol


class IndicatorExitItemSchema(BaseModel):
    """技术指标平仓条件（可多选）：持多仓遇死叉平多、持空仓遇金叉平空"""

    kind: Annotated[
        str,
        Field(description="macd_cross / ma_cross / kdj_cross"),
    ]
    params: Annotated[
        dict[str, Any],
        Field(
            default_factory=dict,
            description="指标参数，缺省用各指标默认值",
        ),
    ]


class FactorExitSchema(BaseModel):
    """因子阈值平仓：按因子仓位意图 pos（tanh 后，∈(-1,1)）触发，多空阈值分设

    - decay 动能衰减（默认）：持多仓 pos ≤ long_threshold 平多；持空仓 pos ≥ -short_threshold 平空
    - reach 达到即平：持多仓 pos ≥ long_threshold 平多（过热离场）；持空仓对称，
      且此时新开仓被抑制（因子已达离仓阈值不追），防止开完立刻平的抖动
    阈值为正数存储，方向按多空各自施加符号。
    """

    mode: Annotated[
        str,
        Field(default="decay", description="decay=动能衰减平仓 / reach=达到即平"),
    ]
    long_threshold: Annotated[
        float,
        Field(
            default=0.15,
            ge=0.01,
            le=0.99,
            description="平多阈值；decay 合法 0.01~0.29，reach 合法 0.35~0.99",
        ),
    ]
    short_threshold: Annotated[
        float,
        Field(
            default=0.15,
            ge=0.01,
            le=0.99,
            description="平空阈值；decay 合法 0.01~0.29，reach 合法 0.35~0.99",
        ),
    ]


class CloseRulesSchema(BaseModel):
    """平仓规则"""

    pnl_pct: Annotated[
        float | None,
        Field(default=None, description="持仓浮盈达到该百分比时平仓，如 5 表示 5%"),
    ]
    total_pnl_pct: Annotated[
        float | None,
        Field(default=None, description="相对任务 baseline 权益涨幅%时平仓"),
    ]
    session_close: Annotated[
        bool,
        Field(default=False, description="收盘/休市前强制平仓"),
    ]
    ai_auto: Annotated[
        bool,
        Field(default=True, description="允许 AI 自主发出平仓指令"),
    ]
    model_exit: Annotated[
        bool,
        Field(
            default=False,
            description=(
                "平仓由决策模型自己决定（仅 decision 策略有意义；"
                "勾选后模型自主判断何时平仓）"
            ),
        ),
    ]
    indicator_exits: Annotated[
        list[IndicatorExitItemSchema] | None,
        Field(
            default=None,
            description=(
                "技术指标平仓条件（可多选）：每根新收盘K线检查，"
                "持多仓遇死叉平多、持空仓遇金叉平空，"
                "硬规则强制执行"
            ),
        ),
    ]
    factor_exit: Annotated[
        FactorExitSchema | None,
        Field(
            default=None,
            description=(
                "因子阈值平仓：量化因子任务用自身公式，AI 任务用挂载的参考因子；"
                "未配置因子时该条件不生效"
            ),
        ),
    ]


class StopRulesSchema(BaseModel):
    """止损规则"""

    loss_pct: Annotated[
        float | None,
        Field(default=None, description="持仓浮亏达到该百分比止损"),
    ]
    loss_amount: Annotated[
        float | None,
        Field(default=None, description="持仓浮亏金额达到该值止损（元）"),
    ]
    ai_auto: Annotated[
        bool,
        Field(default=True, description="允许 AI 自主止损"),
    ]


class MaCrossParamsSchema(BaseModel):
    """双均线参数"""

    fast_period: Annotated[int, Field(default=5, ge=1, le=200, description="快线周期")]
    slow_period: Annotated[int, Field(default=20, ge=2, le=500, description="慢线周期")]
    ma_type: Annotated[str, Field(default="sma", description="sma / ema")]


class CreateAITradingTaskRequest(BaseModel):
    """创建 AI / 量化交易任务"""

    name: Annotated[str, Field(default="", max_length=120)]
    # AI 必填；ma_cross 可省略
    model_row_id: Annotated[
        str | None,
        Field(default=None, description="ai_models 表 UUID（AI 策略必填）"),
    ]
    strategy_type: Annotated[
        str,
        Field(
            default="ai",
            description=(
                "ai / ma_cross / n_breakout / macd_cross / kdj_cross / "
                "band_swing"
            ),
        ),
    ]
    strategy_params: Annotated[
        dict[str, Any] | None,
        Field(default=None, description="策略参数，如均线周期"),
    ]
    icon: Annotated[
        str | None,
        Field(default=None, max_length=48, description="头像图标 slug；空=自动"),
    ]
    symbol: Annotated[CnyFuturesSymbol, Field(min_length=1, max_length=20)]
    symbol_name: Annotated[str, Field(default="", max_length=50)]
    timeframe: Annotated[
        str,
        Field(description="1m/5m/15m/30m/60m/1d"),
    ]
    # 多周期共振：主周期外的附加周期（去重去主周期后最多 3 个，总周期 ≤ 4；仅 AI 策略生效）
    extra_timeframes: Annotated[
        list[str],
        Field(
            default_factory=list,
            max_length=3,
            description="多周期共振附加周期（最多3个，不含主周期，仅 AI 策略生效）",
        ),
    ]
    ai_bars_limit: Annotated[
        int,
        Field(
            default=40,
            ge=10,
            le=240,
            description="AI 决策每个周期查看的 K 线根数（默认40，10-240）",
        ),
    ]
    decision_interval_sec: Annotated[
        int,
        Field(
            default=60,
            ge=60,
            le=300,
            description="决策模型评估间隔秒（60-300，最快1分钟最慢5分钟；仅 decision 策略生效）",
        ),
    ]
    quant_interval_sec: Annotated[
        int,
        Field(
            default=0,
            ge=0,
            le=86400,
            description=(
                "量化任务快频评估间隔秒（全部量化策略生效，decision 用 decision_interval_sec）："
                "0=按K线收盘对齐（默认）；设为 60~K线周期秒数则按间隔评估，"
                "信号计算含实时未收盘bar，上限为该任务K线周期"
            ),
        ),
    ]
    side_mode: Annotated[
        str,
        Field(default="both", description="long_only/short_only/both"),
    ]
    position_mode: Annotated[
        str,
        Field(
            default="fixed_qty",
            description="full/half/fixed_qty/scale_in（滚仓：盈利同向加仓）",
        ),
    ]
    fixed_qty: Annotated[
        int,
        Field(default=1, ge=1, le=10000, description="兼容字段；未传 qty 范围时使用"),
    ]
    qty_min: Annotated[
        int | None,
        Field(default=None, ge=1, le=10000, description="开仓最少手数"),
    ]
    qty_max: Annotated[
        int | None,
        Field(default=None, ge=1, le=10000, description="开仓最多手数"),
    ]
    # AI 资金仓：从用户模拟账户可用资金预留给本任务
    allocated_capital: Annotated[
        float,
        Field(
            default=100000,
            ge=1000,
            le=100_000_000,
            description="划入 AI 资金仓的金额（元），AI 仅在此额度内交易",
        ),
    ]
    capital_usage_min_pct: Annotated[
        float,
        Field(
            default=0,
            ge=0,
            le=100,
            description="资金仓最低使用比例（%），提示 AI 不宜过低仓",
        ),
    ]
    capital_usage_max_pct: Annotated[
        float,
        Field(
            default=100,
            ge=0,
            le=100,
            description="资金仓最高使用比例（%），硬限制保证金预算上限",
        ),
    ]
    # 激进/稳健/保守
    risk_style: Annotated[
        str,
        Field(
            default="balanced",
            description="aggressive/balanced/conservative 或 激进/稳健/保守",
        ),
    ]
    # 交易周期（最长持仓日历天数）：未填则按 K 线周期取默认值
    max_hold_days: Annotated[
        int | None,
        Field(default=None, ge=1, le=365, description="交易周期/最长持仓天数（1-365），空=按周期默认"),
    ]
    # 用户自定义提示词
    custom_prompt_enabled: Annotated[
        bool,
        Field(default=False, description="是否启用用户自定义交易提示词"),
    ]
    custom_prompt: Annotated[
        str | None,
        Field(default=None, max_length=2000, description="用户自定义交易提示词"),
    ]
    close_rules: CloseRulesSchema = Field(default_factory=CloseRulesSchema)
    stop_rules: StopRulesSchema = Field(default_factory=StopRulesSchema)
    close_on_stop: Annotated[bool, Field(default=True)]
    auto_start: Annotated[bool, Field(default=False, description="创建后立即运行")]


class UpdateAITradingTaskRequest(BaseModel):
    """更新任务配置

    仅 status=stopped 且（未下单 或 该品种已全部平仓）时可改。
    """

    name: Annotated[str | None, Field(default=None, max_length=120)]
    model_row_id: Annotated[str | None, Field(default=None, description="ai_models UUID")]
    strategy_params: Annotated[
        dict[str, Any] | None,
        Field(default=None, description="策略参数（量化可改均线）"),
    ]
    icon: Annotated[
        str | None,
        Field(default=None, max_length=48, description="头像图标 slug；空=自动"),
    ]
    symbol: Annotated[CnyFuturesSymbol | None, Field(default=None, max_length=20)]
    symbol_name: Annotated[str | None, Field(default=None, max_length=50)]
    timeframe: Annotated[str | None, Field(default=None)]
    extra_timeframes: Annotated[
        list[str] | None,
        Field(
            default=None,
            max_length=3,
            description="多周期共振附加周期（最多3个，不含主周期）；显式空数组=清空",
        ),
    ]
    ai_bars_limit: Annotated[
        int | None,
        Field(default=None, ge=10, le=240, description="AI 每周期 K 线根数；空=不变"),
    ]
    decision_interval_sec: Annotated[
        int | None,
        Field(default=None, ge=60, le=300, description="决策模型评估间隔秒；空=不变"),
    ]
    quant_interval_sec: Annotated[
        int | None,
        Field(
            default=None,
            ge=0,
            le=86400,
            description="量化任务评估间隔秒；0=恢复按K线收盘对齐；空=不变（全部量化策略生效）",
        ),
    ]
    side_mode: Annotated[str | None, Field(default=None)]
    position_mode: Annotated[str | None, Field(default=None)]
    fixed_qty: Annotated[int | None, Field(default=None, ge=1, le=10000)]
    qty_min: Annotated[int | None, Field(default=None, ge=1, le=10000)]
    qty_max: Annotated[int | None, Field(default=None, ge=1, le=10000)]
    allocated_capital: Annotated[float | None, Field(default=None, ge=1000, le=100_000_000)]
    capital_usage_min_pct: Annotated[float | None, Field(default=None, ge=0, le=100)]
    capital_usage_max_pct: Annotated[float | None, Field(default=None, ge=0, le=100)]
    risk_style: Annotated[str | None, Field(default=None)]
    max_hold_days: Annotated[int | None, Field(default=None, ge=1, le=365, description="交易周期/最长持仓天数；空=不变")]
    custom_prompt_enabled: Annotated[bool | None, Field(default=None)]
    custom_prompt: Annotated[str | None, Field(default=None, max_length=2000)]
    close_rules: CloseRulesSchema | None = None
    stop_rules: StopRulesSchema | None = None
    close_on_stop: Annotated[bool | None, Field(default=None)]


class UpdateTaskRulesRequest(BaseModel):
    """无持仓时调整止盈/止损规则

    不要求任务已结束（running/paused/stopped 均可），
    仅更新 close_rules / stop_rules，不触碰其它配置与任务状态。
    """

    close_rules: CloseRulesSchema | None = None
    stop_rules: StopRulesSchema | None = None


class AITradingTaskResponse(BaseModel):
    """任务详情响应"""

    id: str
    user_id: str
    name: str
    model_row_id: str
    model_id: str | None = None
    model_display_name: str | None = None
    provider_name: str | None = None
    icon: str | None = None
    symbol: str
    symbol_name: str
    timeframe: str
    extra_timeframes: list[str] = []
    ai_bars_limit: int = 40
    decision_interval_sec: int = 60
    quant_interval_sec: int = 0
    side_mode: str
    position_mode: str
    fixed_qty: int
    qty_min: int = 1
    qty_max: int = 1
    allocated_capital: float = 0
    capital_usage_min_pct: float = 0
    capital_usage_max_pct: float = 100
    risk_style: str = "balanced"
    max_hold_days: int = 10
    custom_prompt_enabled: bool = False
    custom_prompt: str | None = None
    position_opened_at: str | None = None
    close_rules: dict[str, Any]
    stop_rules: dict[str, Any]
    status: str
    # 胜率统计（每次平仓后增量更新）
    win_count: int = 0
    loss_count: int = 0
    trade_count: int = 0
    win_rate: float = 0.0
    equity_baseline: float
    close_on_stop: bool
    last_run_at: str | None
    next_bar_ts: str | None
    last_bar_time: str | None
    started_at: str | None
    paused_at: str | None
    stopped_at: str | None
    note: str | None
    created_at: str
    updated_at: str
    cash_delta: float | None = None


class AITradingDecisionResponse(BaseModel):
    """分析/决策记录"""

    id: str
    task_id: str
    bar_time: str | None
    timeframe: str
    trigger_type: str
    context_summary: dict[str, Any] | None
    model_output: dict[str, Any] | None
    action: str
    order_id: str | None
    reason: str
    created_at: str


class EquityPointResponse(BaseModel):
    """权益曲线点"""

    task_id: str
    ts: str
    equity: float
    cash_delta: float
    realized_pnl: float
    unrealized_pnl: float


class EquitySeriesResponse(BaseModel):
    """多任务权益序列"""

    series: dict[str, list[EquityPointResponse]]


class EstimateCapitalRequest(BaseModel):
    """AI 任务仓位资金估算请求"""

    symbol: CnyFuturesSymbol
    qty: int = Field(ge=1, le=10000, description="手数（按该手数估算最大所需）")


class SwitchModelRequest(BaseModel):
    """切换任务模型（接手操盘）请求

    仅更新 model_row_id 及其元数据，不触碰交易配置；
    切换后立即用新模型执行一次决策。
    """

    model_row_id: str = Field(description="新模型的 ai_models.id（UUID 字符串）")
