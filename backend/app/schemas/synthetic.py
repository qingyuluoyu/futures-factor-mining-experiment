"""AI 生成 K 线过拟合测试 —— 请求/响应 schema

供 /api/backtest/generate-kline 与 /api/backtest/step 使用。
与历史回测 (schemas/backtest.py) 完全独立，不修改既有请求模型。
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

# 生成请求 ----------------------------------------------------------

_VALID_TIMEFRAMES = ("1m", "5m", "15m", "30m", "60m", "1d")


class SyntheticGenerateRequest(BaseModel):
    """生成一组 AI 合成 K 线（LLM 剧本 + 程序化随机过程）"""

    model_row_id: Annotated[
        str,
        Field(min_length=1, description="用户模型库中的模型 ID（决定剧本生成）"),
    ]
    symbol: Annotated[
        str,
        Field(
            default="",
            max_length=20,
            description="目标品种代码（决定价格量级/合约参数风格，可空）",
        ),
    ]
    symbol_name: Annotated[str, Field(default="", max_length=50)]
    timeframe: Annotated[str, Field(default="1d", description="1m/5m/15m/30m/60m/1d")]
    num_days: Annotated[
        int,
        Field(
            default=30,
            ge=1,
            le=365,
            description="生成的自然天数（按周期上限裁剪）",
        ),
    ]
    # 可选：前端可传入最近行情价，使生成 K 线贴合该品种真实价格量级
    ref_price: Annotated[
        float | None,
        Field(default=None, gt=0, description="参考价（不传则按品种代码默认）"),
    ]
    seed: Annotated[
        int | None,
        Field(default=None, description="随机种子（便于复现，可不传）"),
    ]


# step 请求 --------------------------------------------------------

class SyntheticStepRequest(BaseModel):
    """边推边决策：在已生成的某根 K 线收盘后请求一次策略决策。

    无状态：前端每推一根 K 线就把"已可见前缀"整体回传，
    后端重建窗口 + VirtualAccount 快照后计算当前 bar 的信号。
    """

    # 当前已推到（含）的 K 线前缀（含 warmup）
    bars_so_far: Annotated[
        list[dict[str, Any]],
        Field(min_length=1, description="已可见的 K 线序列（含预热段）"),
    ]
    # 当前账户快照（上一根结束后的状态；首根传 null）
    account_snapshot: Annotated[
        dict[str, Any] | None,
        Field(default=None, description="上一根的账户快照"),
    ]
    # 策略配置
    strategy_type: Annotated[str, Field(default="n_breakout")]
    strategy_params: Annotated[
        dict[str, Any] | None, Field(default=None)
    ] = None
    side_mode: Annotated[str, Field(default="both")]
    fixed_qty: Annotated[int, Field(default=1, ge=1, le=100)]
    initial_cash: Annotated[float, Field(default=1_000_000, ge=10000)]
    risk_style: Annotated[str, Field(default="balanced")]
    custom_prompt_enabled: Annotated[bool, Field(default=False)]
    custom_prompt: Annotated[str | None, Field(default=None, max_length=2000)]
    close_rules: Annotated[
        dict[str, Any] | None, Field(default=None)
    ] = None
    stop_rules: Annotated[
        dict[str, Any] | None, Field(default=None)
    ] = None
    max_hold_days: Annotated[int, Field(default=10, ge=1)]
    # AI 策略才需要
    model_row_id: Annotated[
        str | None,
        Field(default=None, description="AI 策略模型 ID"),
    ]
    # 决策模型混合模式：量化策略信号喂给决策模型（Jev），模型做最终投资判断
    decision_model_row_id: Annotated[
        str | None,
        Field(
            default=None,
            description="决策模型 ID（TypeSafe Jev）；设置后策略信号作为参考输入",
        ),
    ]
    # 决策模型调用限频：间隔=回放速度（钳制 2000-5000ms）
    decision_min_interval_ms: Annotated[
        int,
        Field(
            default=2000,
            ge=100,
            le=60000,
            description="决策模型最小调用间隔毫秒（实际钳制到 2000-5000）",
        ),
    ]
    last_decision_at: Annotated[
        float | None,
        Field(
            default=None,
            description="上次决策模型调用时刻（epoch ms，上一根 step 回传）；不足间隔的 bar 降级用策略信号",
        ),
    ]
    symbol: Annotated[str, Field(default="", max_length=20)]
    symbol_name: Annotated[str, Field(default="", max_length=50)]
    timeframe: Annotated[str, Field(default="1d")]


class SyntheticFinishRequest(BaseModel):
    """回放结束：用后端 compute_metrics 算最终指标，返回与历史回测一致的结构"""

    equity_curve: Annotated[
        list[dict[str, Any]],
        Field(min_length=1, description="累计权益曲线"),
    ]
    trades: Annotated[list[dict[str, Any]], Field(default_factory=list)]
    account_snapshot: Annotated[
        dict[str, Any] | None, Field(default=None)
    ] = None
    initial_cash: Annotated[float, Field(default=1_000_000, ge=10000)]
    bars: Annotated[
        list[dict[str, Any]],
        Field(min_length=1, description="生成的全部 K 线（用于图表）"),
    ]
    strategy_type: Annotated[str, Field(default="n_breakout")]
    ai_calls: Annotated[int, Field(default=0, ge=0)]
    timeframe: Annotated[str, Field(default="", description="K 线周期，回传给图表正确渲染")]
    symbol: Annotated[str, Field(default="", max_length=20)]
    symbol_name: Annotated[str, Field(default="", max_length=50)]
