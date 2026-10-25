"""因子实验室 API 请求/响应 Schema

cost 统一为可选：为 None 时后端按品种 tick + 手续费推导单边成本率
（见 services/factor_lab/cost.py），避免沿用股票口径的固定 3bp。
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """因子搜索请求（含路线 A 教练扩展）

    防过拟合默认开启推荐配置（train_ratio=0.7, walk_forward_folds=3）：
    - train_ratio>0：按比例切分 bars，搜索只在训练段，测试段独立验证并淘汰负者。
    - test_recent_bars>0：强制最近 N 根作测试段，优先级高于 train_ratio。
    - walk_forward_folds>0：搜索后跑 N 折滚动验证，任一折为负淘汰。
    如需关闭防过拟合（回到改造前行为），把三个参数显式置 0。
    """

    symbol: str
    timeframe: str = "1d"
    population: int = Field(30, ge=10, le=100)
    generations: int = Field(15, ge=3, le=50)
    top_n: int = Field(10, ge=1, le=30)
    seed: int = 42
    cost: float | None = Field(None, ge=0.0, le=0.01)
    seed_tokens: list[list[int]] | None = None
    use_llm_coach: bool = False
    model_row_id: UUID | None = None
    mutation_p: float | None = Field(None, ge=0.05, le=0.8)
    crossover_p: float | None = Field(None, ge=0.2, le=0.9)
    train_ratio: float = Field(0.7, ge=0.0, lt=0.95)
    test_recent_bars: int = Field(0, ge=0, le=2000)
    walk_forward_folds: int = Field(3, ge=0, le=6)
    # 实盘口径门：测试段"次根开盘成交"口径 sortino>0 才晋级（防高换手因子
    # 收盘撮合赚钱、实际开盘成交亏损）。默认关。
    live_fill_gate: bool = False
    # 跨品种验证：严格筛要求 ≥⌈K/2⌉ 个同板块伙伴品种 sortino>0。
    # 因子真有经济含义应跨品种迁移，过拟合在别的品种上现形。默认关。
    cross_validate: bool = False
    # 岛模型：种群分 N 岛独立进化+周期迁移。1=关闭（默认）。
    # E7 实验（RESULTS.md P7 节）：4 岛在 2/3 目标上最优冠军测试段
    # sortino 更差、多样性无增益——默认关闭，仅供实验用途。
    islands: int = Field(1, ge=1, le=8)
    # 可选长历史区间（YYYY-MM-DD，与 end_date 成对）；不传走近期数据（TF_LIMIT）
    start_date: str | None = None
    end_date: str | None = None


class BacktestFactorRequest(BaseModel):
    """单个因子回测请求

    walk_forward_folds>0 时附带滚动多窗口明细，供前端展示因子在不同时点
    偏移下的表现，直观看出"推移到哪些时段会亏"。
    """

    symbol: str
    timeframe: str = "1d"
    factor_tokens: list[int]
    initial_cash: float = 1_000_000.0
    cost: float | None = Field(None, ge=0.0, le=0.01)
    walk_forward_folds: int = Field(0, ge=0, le=6)
    start_date: str | None = None
    end_date: str | None = None


class CompareRequest(BaseModel):
    """因子 A/B 对比请求"""

    symbol: str
    timeframe: str = "1d"
    factor_tokens_list: list[list[int]]
    cost: float | None = Field(None, ge=0.0, le=0.01)
    start_date: str | None = None
    end_date: str | None = None


class FavoriteCreateRequest(BaseModel):
    """创建收藏"""

    tokens: list[int]
    text: str = ""
    name: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    composite: float | None = None
    metrics: dict | None = None
    note: str | None = None
    folder_id: str | None = None


class FavoritePatchRequest(BaseModel):
    """更新收藏名称/备注/文件夹"""

    name: str | None = None
    note: str | None = None
    folder_id: str | None = None


class ReorderRequest(BaseModel):
    """拖拽排序：按顺序的收藏 ID 列表"""

    ids: list[str]


class HistoryChampionItem(BaseModel):
    """本地搜索冠军单条（桌面端 Pyodide 引擎产出）"""

    tokens: list[int]
    text: str = ""
    composite: float = 0.0
    metrics: dict = {}


class HistorySaveRequest(BaseModel):
    """桌面端本地引擎搜索结果保存为历史（source=local，服务端写死）"""

    symbol: str
    timeframe: str = "1d"
    champions: list[HistoryChampionItem]  # 长度 1~20 在端点校验
    trials: int | None = None
    bars: int | None = None
    config: dict | None = None


class LlmGenerateRequest(BaseModel):
    """路线 B：LLM 生成因子"""

    symbol: str
    timeframe: str = "1d"
    model_row_id: UUID
    n_candidates: int = Field(8, ge=3, le=15)
    top_n: int = Field(10, ge=1, le=30)
    cost: float | None = Field(None, ge=0.0, le=0.01)
    seed_tokens: list[list[int]] | None = None
    user_hint: str | None = None
    start_date: str | None = None
    end_date: str | None = None
