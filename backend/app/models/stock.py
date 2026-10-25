"""A 股标的数据模型 —— 标的静态信息 / 每日估值 / 季度财务

支撑「A 股 + 期货」双市场：期货数据走 kline_history(品种代码) + 行情桥，
A 股数据走本文件三张表 + kline_history(6 位股票代码)。
财务表显式记录披露日期 publish_date —— 因子计算只可用发布日及之前的数据，
这是前视偏差（look-ahead bias）防护的关键字段，禁止删除。
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StockInstrument(Base):
    """A 股标的信息表 —— 代码、名称、交易所、行业、上市日期"""

    __tablename__ = "stock_instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        String(10), nullable=False, unique=True, index=True, comment="6位股票代码，如 600519",
    )
    name: Mapped[str] = mapped_column(String(32), nullable=False, comment="股票简称")
    exchange: Mapped[str] = mapped_column(
        String(8), nullable=False, default="", comment="交易所：SSE/SZSE/BSE",
    )
    industry: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", index=True, comment="同花顺行业",
    )
    list_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="上市日期",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow,
    )


class StockValuation(Base):
    """A 股每日估值表 —— PE(TTM)/PB/总市值/流通市值/换手率（AkShare 估值接口）"""

    __tablename__ = "stock_valuation"

    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uq_stock_val_code_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        String(10), nullable=False, index=True, comment="6位股票代码",
    )
    trade_date: Mapped[date] = mapped_column(
        Date, nullable=False, index=True, comment="交易日期",
    )
    pe_ttm: Mapped[float | None] = mapped_column(Float, nullable=True, comment="市盈率TTM")
    pb: Mapped[float | None] = mapped_column(Float, nullable=True, comment="市净率")
    total_mv: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="总市值（亿元）",
    )
    circ_mv: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="流通市值（亿元）",
    )
    turnover_rate: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="换手率（%）",
    )


class StockFinancial(Base):
    """A 股季度财务表 —— ROE/毛利率/营收同比/净利润同比

    report_date 为报告期（如 2026-06-30），publish_date 为实际披露日期。
    因子与回测只允许使用 publish_date <= 决策日 的行，防止用未来才披露的财报。
    """

    __tablename__ = "stock_financial"

    __table_args__ = (
        UniqueConstraint("code", "report_date", name="uq_stock_fin_code_report"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        String(10), nullable=False, index=True, comment="6位股票代码",
    )
    report_date: Mapped[date] = mapped_column(
        Date, nullable=False, index=True, comment="报告期",
    )
    publish_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, index=True, comment="披露日期（前视偏差防护）",
    )
    roe: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="净资产收益率 ROE（%）",
    )
    gross_margin: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="毛利率（%）",
    )
    revenue_yoy: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="营收同比（%）",
    )
    netprofit_yoy: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="净利润同比（%）",
    )
    eps: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="每股收益（元）—— 价值因子 PE 与总股本推导用",
    )
    bps: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="每股净资产（元）—— 价值因子 PB 推导用",
    )
    netprofit: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="归母净利润（元）—— 总股本 = 净利润/EPS",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow,
    )
