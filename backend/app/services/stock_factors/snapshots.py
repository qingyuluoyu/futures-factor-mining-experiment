"""Bounded local research inputs and content fingerprints, without upstream access."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timezone

from sqlalchemy import literal, or_, select, text

from app.models.kline_history import KlineHistory
from app.models.stock import StockFinancial, StockInstrument, StockValuation
from app.services.stock_factors.engine import MarketData, ResearchDataError, _build_market_data
from app.services.stock_factors.financial_selection import FINANCIAL_SELECTION_VERSION, FinancialSelectionError, select_financial_rows
from app.services.stock_factors.dependencies import FINANCIAL_FIELDS, VALUATION_FIELDS, resolve_dependencies

SNAPSHOT_VERSION = "stock-local-snapshot-v1"
SELECTIVE_SNAPSHOT_VERSION = "stock-local-snapshot-v2-selective"
MAX_PRICE_ROWS = 1_000_000
MAX_VALUATION_ROWS = 1_000_000
MAX_FINANCIAL_ROWS = 100_000
MAX_INSTRUMENTS = 10_000
MAX_PANEL_CELLS = 2_000_000


def _invalid(message: str) -> None:
    raise ResearchDataError("invalid_market_data", message)


def _bounded_count(count: int, limit: int, label: str) -> None:
    if count > limit:
        raise ResearchDataError("snapshot_limit_exceeded", f"研究快照{label}超过安全上限 {limit}", 422)


def _validate_rows(rows, *, date_positions, numeric_start, unique_positions, positive=False):
    observed = set()
    for row in rows:
        if not isinstance(row[0], str) or not re.fullmatch(r"[0-9]{6}", row[0]):
            _invalid("本地股票证券标识必须为六位数字代码")
        for position in date_positions:
            if type(row[position]) is not date:
                _invalid("本地股票数据日期缺失或类型错误")
        key = tuple(row[position] for position in unique_positions)
        if key in observed:
            _invalid("本地股票数据存在重复或冲突记录")
        observed.add(key)
        for value in row[numeric_start:]:
            if value is None and not positive:
                continue
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                _invalid("本地股票数据包含非有限数值或错误类型")
            if positive and value <= 0:
                _invalid("本地股票数据包含非正收盘价")


def _fingerprint(start_date, end_date, price_rows, valuation_rows, financial_rows, instruments, *, input_fields=None):
    digest = hashlib.sha256()
    for item in (SELECTIVE_SNAPSHOT_VERSION if input_fields is not None else SNAPSHOT_VERSION, FINANCIAL_SELECTION_VERSION, start_date.isoformat(), end_date.isoformat()):
        digest.update(json.dumps(item, ensure_ascii=False).encode("utf-8") + b"\n")
    if input_fields is not None:
        digest.update(json.dumps(input_fields, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
    for label, rows in (("prices", price_rows), ("valuations", valuation_rows), ("financials", financial_rows), ("instruments", instruments)):
        digest.update(label.encode("ascii") + b"\n")
        for row in rows:
            normalized = [value.isoformat() if isinstance(value, date) else value for value in row]
            digest.update(json.dumps(normalized, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n")
    return digest.hexdigest()


async def _verify_transaction_settings(session, dialect: str) -> dict:
    if dialect == "sqlite":
        return {
            "isolation_level": "serializable",
            "read_only": False,
            "verification": "sqlite_explicit_transaction_select_only",
        }
    isolation_level = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
    read_only = (await session.execute(text("SHOW transaction_read_only"))).scalar_one()
    if isolation_level != "repeatable read" or read_only != "on":
        raise ResearchDataError("snapshot_transaction_unsafe", "研究快照事务未启用可重复读和只读保护", 503)
    return {
        "isolation_level": isolation_level,
        "read_only": read_only == "on",
        "verification": "postgresql_show_transaction_settings",
    }


async def load_versioned_market_data(session_factory, start_date: date, end_date: date, *, factor_ids=None, mktcap_neutral=False, weight="equal") -> tuple[MarketData, dict]:
    """Read one transaction snapshot; its digest is not an immutable or PIT archive."""
    if type(start_date) is not date or type(end_date) is not date or start_date > end_date or (end_date - start_date).days > 3660:
        raise ResearchDataError("invalid_date_range", "研究快照日期范围无效或超过3660天", 422)
    plan = resolve_dependencies(factor_ids, mktcap_neutral=mktcap_neutral, weight=weight)
    read_at = datetime.now(timezone.utc).isoformat()
    async with session_factory() as session:
        dialect = session.get_bind().dialect.name
        if dialect not in {"postgresql", "sqlite"}:
            raise ResearchDataError("snapshot_backend_unsupported", "研究快照需要PostgreSQL一致性事务", 503)
        async with session.begin():
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ" if dialect == "postgresql" else "SERIALIZABLE"})
            if dialect == "postgresql":
                await session.execute(text("SET TRANSACTION READ ONLY"))
            else:
                await session.execute(text("BEGIN"))
            transaction_settings = await _verify_transaction_settings(session, dialect)
            price_rows = (await session.execute(
                select(KlineHistory.code, KlineHistory.trade_date, KlineHistory.close)
                .where(KlineHistory.market_type == "stock", KlineHistory.trade_date >= start_date, KlineHistory.trade_date <= end_date)
                .order_by(KlineHistory.trade_date, KlineHistory.code)
                .limit(MAX_PRICE_ROWS + 1)
            )).all()
            _bounded_count(len(price_rows), MAX_PRICE_ROWS, "价格行数")
            if not price_rows:
                raise ResearchDataError("missing_market_data", "无行情数据，请先导入 A 股数据", 404)
            _validate_rows(price_rows, date_positions=(1,), numeric_start=2, unique_positions=(0, 1), positive=True)
            codes = sorted({row[0] for row in price_rows})
            _bounded_count(len(codes), MAX_INSTRUMENTS, "证券数量")
            price_cells = len({row[1] for row in price_rows}) * len(codes)
            _bounded_count(price_cells, MAX_PANEL_CELLS, "面板格数")
            valuation_rows = (await session.execute(
                select(StockValuation.code, StockValuation.trade_date, *(getattr(StockValuation, field) if field in plan.valuation_fields else literal(None).label(field) for field in VALUATION_FIELDS))
                .where(StockValuation.code.in_(codes), StockValuation.trade_date >= start_date, StockValuation.trade_date <= end_date)
                .order_by(StockValuation.trade_date, StockValuation.code)
                .limit(MAX_VALUATION_ROWS + 1)
            )).all() if plan.valuation_fields else []
            _bounded_count(len(valuation_rows), MAX_VALUATION_ROWS, "估值行数")
            financial_rows = (await session.execute(
                select(StockFinancial.code, StockFinancial.report_date, StockFinancial.publish_date, *(getattr(StockFinancial, field) if field in plan.financial_fields else literal(None).label(field) for field in FINANCIAL_FIELDS))
                .where(StockFinancial.code.in_(codes), StockFinancial.publish_date <= end_date, StockFinancial.report_date <= end_date)
                .order_by(StockFinancial.code, StockFinancial.publish_date, StockFinancial.report_date)
                .limit(MAX_FINANCIAL_ROWS + 1)
            )).all() if plan.financial_fields else []
            _bounded_count(len(financial_rows), MAX_FINANCIAL_ROWS, "财务行数")
            instruments = (await session.execute(
                select(StockInstrument)
                .where(StockInstrument.code.in_(codes), or_(StockInstrument.list_date.is_(None), StockInstrument.list_date <= end_date))
                .order_by(StockInstrument.code)
                .limit(MAX_INSTRUMENTS + 1)
            )).scalars().all()
            _bounded_count(len(instruments), MAX_INSTRUMENTS, "证券元数据数量")
            instrument_rows = [(instrument.code, instrument.name, instrument.exchange, instrument.industry, instrument.list_date) for instrument in instruments]
            _validate_rows(valuation_rows, date_positions=(1,), numeric_start=2, unique_positions=(0, 1))
            try:
                selected_financial_rows = select_financial_rows(financial_rows)
            except FinancialSelectionError as error:
                _invalid(str(error))
            if len({row[0] for row in instrument_rows}) != len(instrument_rows):
                _invalid("证券元数据包含重复代码")
            for code, name, exchange, industry, listed in instrument_rows:
                if exchange not in {"SSE", "SZSE", "BSE", ""} or not isinstance(name, str) or not isinstance(industry, str):
                    _invalid("证券元数据的交易所、名称或行业无效")
            valuation_cells = len({row[0] for row in valuation_rows}) * len({row[1] for row in valuation_rows})
            total_panel_cells = plan.panel_cells(price_cells, valuation_cells, bool(financial_rows))
            _bounded_count(total_panel_cells, MAX_PANEL_CELLS, "合计面板格数")
            data_version = _fingerprint(start_date, end_date, price_rows, valuation_rows, financial_rows, instrument_rows, input_fields=plan.manifest() if plan.selective else None)
            market = _build_market_data(price_rows, valuation_rows, selected_financial_rows, instruments, valuation_fields=plan.valuation_fields, financial_fields=plan.financial_fields)
    manifest = {
        "snapshot_version": SELECTIVE_SNAPSHOT_VERSION if plan.selective else SNAPSHOT_VERSION,
        **({"dependencies": plan.manifest()} if plan.selective else {}),
        "financial_selection_version": FINANCIAL_SELECTION_VERSION,
        "financial_rows": {"raw": len(financial_rows), "selected": len(selected_financial_rows)},
        "data_version": data_version,
        "source": "local_authorized_import_database",
        "provider": "local_import_provenance_not_retained_per_row",
        "license_scope": "local_import_authorization_requires_operator_verification",
        "market_type": "stock",
        "market": "CN_A",
        "exchanges": sorted({row[2] for row in instrument_rows if row[2]}),
        "security_identifier": "six_digit_local_code_with_current_exchange_metadata",
        "currency": "CNY",
        "units": {"close": "CNY/share", "pe_ttm": "ratio", "pb": "ratio", "turnover_rate": "percent", "total_mv": "100_million_CNY", "roe": "percent", "gross_margin": "percent", "revenue_yoy": "percent", "netprofit_yoy": "percent"},
        "timezone": "Asia/Shanghai",
        "frequency": "daily",
        "adjustment": "forward_adjusted_unversioned",
        "financial_basis": "latest_disclosed_report_frontier_aligned_strictly_after_publish_date",
        "trading_calendar": "observed_local_dates_not_completeness_certified",
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "row_counts": {"prices": len(price_rows), "valuations": len(valuation_rows), "financials": len(financial_rows), "instruments": len(instruments)},
        "coverage": {"securities": len(codes), "observed_dates": len(market.close.index), "first_observed_date": market.close.index[0].date().isoformat(), "last_observed_date": market.close.index[-1].date().isoformat(), "missing_price_cells": price_cells - len(price_rows), "missing_instrument_metadata": len(codes) - len(instruments), "panel_cells": total_panel_cells},
        "collected_at": None,
        "snapshot_read_at": read_at,
        "transaction_isolation": "repeatable_read_read_only" if dialect == "postgresql" else "sqlite_serializable_explicit_transaction",
        "transaction": transaction_settings,
        "immutable_archive": False,
        "point_in_time_certified": False,
        "validation_limits": [
            "内容指纹覆盖本次实际输入和当前证券分类名称；不等于不可变原始数据归档或历史时点认证。",
            "当前名称和行业不是历史分类；前复权和历史财报修订版本未留存，存在幸存者和修订偏差。",
            "本地库未逐行留存供应商授权、采集时间与公告原文，不能据指纹推定数据授权或新鲜度已核验。",
            "财报保留研究开始前已公告值，公告当日不可用；同日多报告期选最大报告期，后续披露旧报告期不倒退已可获得报告期。",
            "缺价保留为空；交易日历完整性与可实盘成交条件未验证。",
        ],
    }
    return market, manifest
