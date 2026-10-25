"""因子实验室持久化模型 —— 历史（每合约 FIFO）、收藏、试验登记"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FactorHistory(Base):
    """因子搜索历史 —— 每用户每合约最多 10 条（服务层 FIFO）"""

    __tablename__ = "factor_history"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "symbol",
            "tokens_key",
            name="uq_factor_history_user_symbol_tokens",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="历史记录 ID",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属用户",
    )
    symbol: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="合约代码（小写）",
    )
    timeframe: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="K 线周期",
    )
    tokens: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        comment="因子 token 序列",
    )
    tokens_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="tokens 去重键",
    )
    text: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="可读表达式",
    )
    composite: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        comment="综合分",
    )
    metrics: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="评估指标",
    )
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="gp",
        comment="来源 gp/evolve/llm_gen/manual/local",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="首次写入",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="最近更新（FIFO 用）",
    )


class FactorFavorite(Base):
    """用户收藏的因子配方 —— 交易创建下拉数据源"""

    __tablename__ = "factor_favorites"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "tokens_key",
            name="uq_factor_favorite_user_tokens",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="收藏 ID",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属用户",
    )
    symbol: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="可选关联合约",
    )
    timeframe: Mapped[str | None] = mapped_column(
        String(8),
        nullable=True,
        comment="可选周期",
    )
    name: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        comment="用户命名",
    )
    tokens: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        comment="因子 token 序列",
    )
    tokens_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="tokens 去重键",
    )
    text: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="可读表达式",
    )
    composite: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="收藏时综合分快照",
    )
    metrics: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="收藏时指标快照",
    )
    note: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="备注",
    )
    health: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="衰减监控：250根滚动IC口径的健康度与衰减标记",
    )
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategy_folders.id", ondelete="SET NULL"),
        nullable=True,
        comment="所属收藏文件夹（空=未分类）",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="手动排序（小在前，默认 0 按收藏时间倒序）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="收藏时间",
    )


class FactorSearchTrial(Base):
    """试验登记 —— 每次搜索的总试验数与冠军指标快照

    多重检验修正（PBO / Deflated Sharpe）的原料：样本内指标是
    trials 次尝试的最优值，不登记试验数就无法量化选择偏差。
    gp/evolve/llm_gen/mining 四个来源都写入。best-effort，不影响主流程。
    """

    __tablename__ = "factor_search_trials"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="登记 ID",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属用户",
    )
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="来源：gp/evolve/llm_gen/mining",
    )
    symbol: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="合约代码（小写）",
    )
    timeframe: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="K 线周期",
    )
    trials: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="总评估次数（population×generations + 种子）",
    )
    bars: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="评估用 K 线根数",
    )
    config: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="搜索配置快照（种群/代数/切分/WF 折数/成本）",
    )
    champions: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="冠军精简指标 [{tokens, composite, train_ann, test_ann, wf_stable}]",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True,
        comment="搜索完成时间",
    )
