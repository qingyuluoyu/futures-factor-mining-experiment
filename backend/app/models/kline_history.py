"""K 线历史日线模型 —— PostgreSQL 持久化 AKShare 导入的日线数据"""

from datetime import date

from sqlalchemy import String, Date, Float, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KlineHistory(Base):
    """K 线日线历史表 —— 按品种代码存储，支持连续合约的长期日线"""

    __tablename__ = "kline_history"

    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uq_code_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="futures", server_default="futures",
        index=True, comment="市场：futures=期货 / stock=A股（双市场隔离）",
    )
    code: Mapped[str] = mapped_column(
        String(10), nullable=False, index=True, comment="品种代码，如 AU、RB；A股为6位代码",
    )
    trade_date: Mapped[date] = mapped_column(
        Date, nullable=False, index=True, comment="交易日期",
    )
    open: Mapped[float] = mapped_column(Float, nullable=False, comment="开盘价")
    high: Mapped[float] = mapped_column(Float, nullable=False, comment="最高价")
    low: Mapped[float] = mapped_column(Float, nullable=False, comment="最低价")
    close: Mapped[float] = mapped_column(Float, nullable=False, comment="收盘价")
    volume: Mapped[int] = mapped_column(Integer, nullable=False, comment="成交量")
    hold: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="持仓量",
    )
