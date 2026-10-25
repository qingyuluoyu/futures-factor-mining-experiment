"""K 线分钟线模型 —— PostgreSQL 持久化 TqSdk 导入的历史分钟线

与日线表 kline_history 分离：粒度（带时分秒）、唯一键（含 period）、数据量
（分钟线是日线数百倍）都不同，独立表便于索引/清理策略。
本期只入库 1m，5/15/30/60m 由 aggregate_from_1m 现算，不冗余存储。
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Float, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KlineMinute(Base):
    """K 线分钟线历史表 —— 按品种代码 + 周期存储"""

    __tablename__ = "kline_minute"

    __table_args__ = (
        UniqueConstraint("code", "period", "ts", name="uq_code_period_ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        String(10), nullable=False, index=True, comment="品种代码，如 AU、RB",
    )
    symbol: Mapped[str] = mapped_column(
        String(30), nullable=False, comment="具体合约符号，如 au2412",
    )
    period: Mapped[str] = mapped_column(
        String(4), nullable=False, default="1m", index=True, comment="周期，默认 1m",
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True, comment="K线时间戳",
    )
    open: Mapped[float] = mapped_column(Float, nullable=False, comment="开盘价")
    high: Mapped[float] = mapped_column(Float, nullable=False, comment="最高价")
    low: Mapped[float] = mapped_column(Float, nullable=False, comment="最低价")
    close: Mapped[float] = mapped_column(Float, nullable=False, comment="收盘价")
    volume: Mapped[int] = mapped_column(Integer, nullable=False, comment="成交量")
    hold: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="持仓量",
    )
