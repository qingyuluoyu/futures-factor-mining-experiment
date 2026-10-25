"""A 股行情 API —— 股票池 / K线 / 估值 / 财务（双市场之 stock 侧）

数据全部来自本地库（import-stock-data.py 导入）：
    stock_instruments  标的静态信息
    kline_history      前复权日线（code=6位）
    stock_valuation    每日估值
    stock_financial    季度财务（含公告日期）
"""

import asyncio
import json
import logging
import re
from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.core.market_capability import require_stock_market
from app.models.stock import StockFinancial, StockInstrument, StockValuation
from app.models.user import User

router = APIRouter(prefix="/api/stock", tags=["stock"], dependencies=[Depends(require_stock_market)])
logger = logging.getLogger("qihuo.stock")


def _parse_query_date(value: str | None) -> date | None:
    if value is None or value == "":
        return None
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("日期必须为 YYYY-MM-DD")
    return date.fromisoformat(value)


StockQueryDate = Annotated[date | None, BeforeValidator(_parse_query_date)]


async def _stock_query_page(
    session: AsyncSession, statement: Any, response: Response, user: User,
    *, operation: str, code: str, limit: int, offset: int, date_field: str,
    ascending_result: bool, units: dict[str, str], date_semantics: dict[str, str],
) -> list[Any]:
    request_id = uuid4().hex
    headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
    try:
        async with asyncio.timeout(12):
            records = list((await session.execute(statement.offset(offset).limit(limit + 1))).scalars().all())
    except Exception as error:
        logger.error("request_id=%s user_id=%s operation=%s error_type=%s", request_id, user.id, operation, type(error).__name__)
        raise HTTPException(503, "股票数据读取失败，请凭请求编号核查；未使用空数据代替错误", headers=headers) from None
    has_more = len(records) > limit
    rows = records[:limit]
    if ascending_result:
        rows.reverse()
    headers.update({
        "X-Page-Limit": str(limit), "X-Page-Offset": str(offset),
        "X-Page-Returned": str(len(rows)), "X-Page-Has-More": str(has_more).lower(),
        "X-Page-Order": f"{date_field}_desc",
        "X-Result-Order": f"{date_field}_{'asc' if ascending_result else 'desc'}",
        "X-Data-Metadata": json.dumps({
            "market": "CN-A", "security_code": code, "exchange": "unresolved",
            "currency": "CNY", "timezone": "Asia/Shanghai",
            "frequency": "daily" if operation == "stock_valuation" else "quarterly",
            "source": "local_import", "supplier": "unknown", "provenance": "legacy_unverified",
            "license": "unverified", "data_version": None, "collected_at": None,
            "trading_calendar": "unverified", "price_adjustment": "not_applicable",
            "financial_basis": "unverified", "point_in_time_certified": False,
            "unit_status": "schema_declared_not_row_verified", "units": units,
            "date_semantics": date_semantics,
        }, separators=(",", ":")),
    })
    response.headers.update(headers)
    logger.info("request_id=%s user_id=%s operation=%s code=%s limit=%s offset=%s returned=%s has_more=%s", request_id, user.id, operation, code, limit, offset, len(rows), has_more)
    return rows


@router.get("/universe")
async def list_universe(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
    industry: str = Query("", max_length=100, description="行业过滤，空=全部"),
    keyword: str = Query("", max_length=64, description="代码/名称模糊搜索"),
    limit: int = Query(0, ge=0, le=3000, description="返回数量，0=最多3000只"),
    offset: int = Query(0, ge=0, le=100000, description="分页偏移"),
) -> list[dict[str, Any]]:
    """股票池列表（代码/名称/交易所/行业）"""
    stmt = select(StockInstrument).order_by(StockInstrument.code)
    if industry:
        stmt = stmt.where(StockInstrument.industry == industry)
    if keyword:
        from sqlalchemy import or_

        stmt = stmt.where(or_(
            StockInstrument.code.icontains(keyword, autoescape=True),
            StockInstrument.name.icontains(keyword, autoescape=True),
        ))
    stmt = stmt.offset(offset).limit(limit or 3000)
    rows = (await session.execute(stmt)).scalars().all()
    result = [
        {
            "code": r.code,
            "name": r.name,
            "exchange": r.exchange,
            "industry": r.industry,
            "list_date": r.list_date.isoformat() if r.list_date else None,
        }
        for r in rows
    ]
    if industry:
        result = [r for r in result if r["industry"] == industry]
    if keyword:
        kw = keyword.lower()
        result = [
            r for r in result
            if kw in r["code"].lower() or kw in r["name"].lower()
        ]
    if limit > 0:
        result = result[:limit]
    return result


@router.get("/industries")
async def list_industries(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """行业列表（含股票数）—— 品种树/行业中性化用"""
    rows = (await session.execute(select(StockInstrument))).scalars().all()
    counter: dict[str, int] = {}
    for r in rows:
        key = r.industry or "未分类"
        counter[key] = counter.get(key, 0) + 1
    return [
        {"industry": k, "count": v}
        for k, v in sorted(counter.items(), key=lambda kv: -kv[1])
    ]


KlineSource = Literal["legacy_auto", "history_qfq", "eod_raw"]


class KlineSourceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Literal["history_qfq", "eod_raw"]
    source: Literal["local_import", "teajoin_eod"]
    price_adjustment: Literal["qfq", "none"]
    available: bool
    reason: Literal["available", "no_local_history", "eod_disabled", "instrument_not_found"]
    available_bars: int | None = Field(ge=0)
    first_date: date | None
    last_date: date | None
    window_cap: Literal[250, 3000]
    history_completeness: Literal["unverified"] = "unverified"


class KlineSources(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog_version: Literal["stock-kline-sources-v1"] = "stock-kline-sources-v1"
    code: str = Field(pattern=r"^[0-9]{6}$")
    default_mode: Literal["legacy_auto"] = "legacy_auto"
    sources: list[KlineSourceEntry] = Field(min_length=2, max_length=2)


async def _history_kline_coverage(session, code: str, today: date):
    from app.models.kline_history import KlineHistory
    count, first, last = (await session.execute(select(func.count(), func.min(KlineHistory.trade_date), func.max(KlineHistory.trade_date)).where(
        KlineHistory.code == code, KlineHistory.market_type == "stock", KlineHistory.trade_date <= today))).one()
    return count, first.isoformat() if first else None, last.isoformat() if last else None


@router.get("/kline/sources", response_model=KlineSources)
async def stock_kline_sources(request: Request, response: Response,
    code: str = Query(..., pattern=r"^[0-9]{6}$"), session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user)):
    request_id = uuid4().hex
    headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
    try:
        async with asyncio.timeout(12):
            exists = await session.scalar(select(StockInstrument.code).where(StockInstrument.code == code))
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            history = await _history_kline_coverage(session, code, today) if exists else (0, None, None)
            eod_enabled = bool(getattr(request.app.state, "stock_eod_enabled", False))
            if exists and eod_enabled:
                from app.services.stock_eod import eod_coverage
                raw = await eod_coverage(session, code, today)
            else:
                raw = (None, None, None) if exists and not eod_enabled else (0, None, None)
            sources = []
            for name, source, adjustment, cap, summary in (("history_qfq", "local_import", "qfq", 3000, history), ("eod_raw", "teajoin_eod", "none", 250, raw)):
                count, first, last = summary
                reason = "instrument_not_found" if not exists else "eod_disabled" if name == "eod_raw" and not eod_enabled else "available" if count else "no_local_history"
                sources.append(KlineSourceEntry(id=name, source=source, price_adjustment=adjustment, available=reason == "available", reason=reason,
                    available_bars=count, first_date=first, last_date=last, window_cap=cap))
            result = KlineSources(code=code, sources=sources)
    except Exception as error:
        logger.error("request_id=%s user_id=%s operation=stock_kline_sources error_type=%s", request_id, user.id, type(error).__name__)
        raise HTTPException(503, "日线来源目录读取失败；未使用空目录代替错误", headers=headers) from None
    response.headers.update(headers)
    logger.info("request_id=%s user_id=%s operation=stock_kline_sources code=%s source_calls=0", request_id, user.id, code)
    return result


@router.get("/kline")
async def stock_kline(
    request: Request,
    response: Response,
    code: str = Query(..., pattern=r"^[0-9]{6}$", description="6位ASCII股票代码，如 600519"),
    limit: int = Query(250, ge=1, le=3000, description="返回最近 N 根"),
    source: KlineSource = Query("legacy_auto"),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """独立复权日线；没有研究历史时才读取明确标识的不复权盘后序列。"""
    request_id = uuid4().hex
    headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
    try:
        async with asyncio.timeout(12):
            result = await _stock_kline_data(session, code, limit, prefer_eod=getattr(request.app.state, "stock_eod_enabled", False), source=source)
    except HTTPException as error:
        raise HTTPException(error.status_code, error.detail, headers={**headers, **(error.headers or {})}) from None
    except Exception as error:
        logger.error("request_id=%s user_id=%s operation=stock_kline error_type=%s", request_id, _user.id, type(error).__name__)
        raise HTTPException(503, "股票K线读取失败，请凭请求编号核查；未使用空行情代替错误", headers=headers) from None
    for name, value in headers.items():
        response.headers[name] = value
    logger.info("request_id=%s user_id=%s operation=stock_kline code=%s bars=%s source=%s", request_id, _user.id, code, len(result["bars"]), result.get("source", "no_instrument"))
    return result


async def _stock_kline_data(session: AsyncSession, code: str, limit: int, *, prefer_eod: bool, source: KlineSource = "legacy_auto") -> dict:
    if source == "eod_raw" and not prefer_eod:
        raise HTTPException(503, "盘后原价来源未开放，未改读其他来源", headers={"X-Kline-Error": "source_disabled"})
    stmt = (
        select(StockInstrument)
        .where(StockInstrument.code == code)
    )
    instrument = (await session.execute(stmt)).scalar_one_or_none()
    if instrument is None:
        if source != "legacy_auto":
            raise HTTPException(404, "股票目录未收录该证券", headers={"X-Kline-Error": "instrument_not_found"})
        return {"code": code, "name": code, "industry": "", "bars": [], "has_more": False}

    from app.models.kline_history import KlineHistory

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    conditions = (
        KlineHistory.code == code,
        KlineHistory.market_type == "stock",
        KlineHistory.trade_date <= today,
    )
    stmt = (
        select(KlineHistory)
        .where(*conditions)
        .order_by(KlineHistory.trade_date.desc())
        .limit(limit + 1)
    )
    bars = (await session.execute(stmt)).scalars().all() if source != "eod_raw" else []
    has_more = len(bars) > limit
    bars = list(reversed(bars[:limit]))
    if source == "eod_raw" or (not bars and prefer_eod and source == "legacy_auto"):
        from app.services.stock_eod import eod_bars, eod_coverage

        raw_bars = await eod_bars(session, code, today, limit, include_reference=True)
        raw_limit = min(limit, 250)
        if not raw_bars and source == "eod_raw":
            raise HTTPException(409, "所选盘后原价来源尚无本地日线，未改读其他来源", headers={"X-Kline-Error": "no_local_history"})
        coverage = await eod_coverage(session, code, today)
        return {"code": code, "name": instrument.name, "industry": instrument.industry,
            "bars": raw_bars[-raw_limit:], "has_more": len(raw_bars) > raw_limit,
            **({"selected_source": source} if source != "legacy_auto" else {}),
            "coverage": _stock_kline_coverage(coverage, len(raw_bars[-raw_limit:]), limit, raw_limit, len(raw_bars) > raw_limit),
            "price_adjustment": "none", "source": "teajoin_eod", "volume_unit": "lot_100_shares",
            "reference_price": "vendor_ex_rights_pre_close", "change_basis": "vendor_ex_rights_pre_close"}
    if not bars and source == "history_qfq":
        raise HTTPException(409, "所选历史前复权来源尚无本地日线，未改读其他来源", headers={"X-Kline-Error": "no_local_history"})
    coverage = await _history_kline_coverage(session, code, today)
    return {
        "code": code,
        "name": instrument.name,
        "industry": instrument.industry,
        "bars": [
            {
                "time": b.trade_date.isoformat(),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ],
        "has_more": has_more,
        **({"selected_source": source} if source != "legacy_auto" else {}),
        "coverage": _stock_kline_coverage(coverage, len(bars), limit, limit, has_more),
        "price_adjustment": "qfq", "source": "local_import", "volume_unit": "lot_100_shares",
        "change_basis": "adjacent_loaded_adjusted_close",
    }


def _stock_kline_coverage(coverage: tuple[int, str | None, str | None], returned: int, requested: int, effective: int, has_more: bool) -> dict:
    count, first, last = coverage
    # Concurrent data changes must not publish contradictory range/count metadata.
    if count < returned or has_more != (count > returned):
        raise ValueError("日线窗口与本地覆盖统计不一致，请重新读取")
    return {"scope": "selected_source_local_store", "available_bars": count,
        "first_date": first, "last_date": last, "requested_limit": requested, "effective_limit": effective,
        "window_status": "no_local_history" if count == 0 else "window_limited" if has_more else "local_history_exhausted",
        "history_completeness": "unverified"}


@router.get("/valuation")
async def stock_valuation(
    response: Response,
    code: str = Query(..., pattern=r"^[0-9]{6}$", description="6位ASCII股票代码"),
    start: Annotated[StockQueryDate, Query(description="起始交易日 YYYY-MM-DD，含当日；空字符串兼容无边界")] = None,
    end: Annotated[StockQueryDate, Query(description="结束交易日 YYYY-MM-DD，含当日；空字符串兼容无边界")] = None,
    limit: int = Query(3000, ge=1, le=3000, description="最新估值窗口大小；窗口内按交易日升序返回"),
    offset: int = Query(0, ge=0, le=100000, description="按交易日降序窗口偏移；连续读取期间应固定end"),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """每日估值；分页和旧导入数据的证据边界见 X-Page-* / X-Data-Metadata。"""
    if start is not None and end is not None and start > end:
        raise HTTPException(422, "起始交易日不得晚于结束交易日")
    stmt = select(StockValuation).where(StockValuation.code == code)
    if start is not None:
        stmt = stmt.where(StockValuation.trade_date >= start)
    if end is not None:
        stmt = stmt.where(StockValuation.trade_date <= end)
    stmt = stmt.order_by(StockValuation.trade_date.desc(), StockValuation.id.desc())
    rows = await _stock_query_page(
        session, stmt, response, _user, operation="stock_valuation", code=code,
        limit=limit, offset=offset, date_field="trade_date", ascending_result=True,
        units={"pe_ttm": "ratio", "pb": "ratio", "total_mv": "CNY_100_million", "circ_mv": "CNY_100_million", "turnover_rate": "percent"},
        date_semantics={"trade_date": "trading_date"},
    )
    return [
        {
            "trade_date": r.trade_date.isoformat(),
            "pe_ttm": r.pe_ttm,
            "pb": r.pb,
            "total_mv": r.total_mv,
            "circ_mv": r.circ_mv,
            "turnover_rate": r.turnover_rate,
        }
        for r in rows
    ]


@router.get("/financials")
async def stock_financials(
    response: Response,
    code: str = Query(..., pattern=r"^[0-9]{6}$", description="6位ASCII股票代码"),
    limit: int = Query(40, ge=1, le=200, description="按报告期倒序返回，默认最近40期"),
    offset: int = Query(0, ge=0, le=100000, description="按报告期倒序分页偏移"),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """季度财务展示；报告期不等于披露日，旧数据未获历史时点认证。"""
    stmt = (
        select(StockFinancial)
        .where(StockFinancial.code == code)
        .order_by(StockFinancial.report_date.desc(), StockFinancial.id.desc())
    )
    rows = await _stock_query_page(
        session, stmt, response, _user, operation="stock_financials", code=code,
        limit=limit, offset=offset, date_field="report_date", ascending_result=False,
        units={"roe": "percent", "gross_margin": "percent", "revenue_yoy": "percent", "netprofit_yoy": "percent", "eps": "CNY_per_share", "bps": "CNY_per_share", "netprofit": "CNY"},
        date_semantics={"report_date": "report_period_end", "publish_date": "disclosure_date_nullable_unverified"},
    )
    return [
        {
            "report_date": r.report_date.isoformat(),
            "publish_date": r.publish_date.isoformat() if r.publish_date else None,
            "roe": r.roe,
            "gross_margin": r.gross_margin,
            "revenue_yoy": r.revenue_yoy,
            "netprofit_yoy": r.netprofit_yoy,
            "eps": r.eps,
            "bps": r.bps,
            "netprofit": r.netprofit,
        }
        for r in rows
    ]


@router.get("/data-status")
async def stock_data_status(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """A 股数据覆盖情况（导入是否完成 / 数据日期范围）"""
    from sqlalchemy import func

    from app.models.kline_history import KlineHistory

    inst_count = (
        await session.execute(select(func.count(StockInstrument.code)))
    ).scalar() or 0
    val_count = (
        await session.execute(select(func.count(StockValuation.id)))
    ).scalar() or 0
    fin_count = (
        await session.execute(select(func.count(StockFinancial.id)))
    ).scalar() or 0
    kline_count = (
        await session.execute(
            select(func.count(KlineHistory.id)).where(
                KlineHistory.market_type == "stock"
            )
        )
    ).scalar() or 0
    latest = (
        await session.execute(
            select(func.max(KlineHistory.trade_date)).where(
                KlineHistory.market_type == "stock"
            )
        )
    ).scalar()
    return {
        "instruments": inst_count,
        "valuation_rows": val_count,
        "financial_rows": fin_count,
        "kline_rows": kline_count,
        "latest_trade_date": latest.isoformat() if latest else None,
    }
