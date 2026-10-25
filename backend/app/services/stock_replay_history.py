from datetime import datetime, timezone
from decimal import Decimal
import re
from uuid import UUID

from sqlalchemy import func, select, text

from app.models.stock_replay import StockReplayAccount, StockReplayOrder
from app.schemas.stock_replay_history import ReplayHistoryQuery
from app.services.stock_replay_engine import ReplayError, RULES_VERSION


HISTORY_VERSION = "stock-replay-order-history-v1"


def invalid():
    return ReplayError("invalid_order_history", "已存模拟委托的金额、日期或状态不一致；未填零或丢弃异常记录", 409)


def amount(value, scale, *, nullable=False, positive=False, nonnegative=False):
    if value is None and nullable:
        return None
    if not isinstance(value, Decimal) or not value.is_finite() or value.as_tuple().exponent < -scale or abs(value) >= Decimal(10) ** (18 - scale):
        raise invalid()
    if (positive and value <= 0) or (nonnegative and value < 0):
        raise invalid()
    return format(value, "f")


def order_row(row, account, dialect):
    if row.code not in account.datasets_json or row.direction not in ("buy", "sell") or row.status not in ("pending", "filled", "rejected", "cancelled") or row.order_type not in ("market", "limit"):
        raise invalid()
    if not 0 < row.quantity <= 1000000 or row.quantity % 100 or not account.start_date <= row.submitted_on <= account.current_date:
        raise invalid()
    if (row.order_type == "limit") != (row.limit_price is not None) or (row.reason is not None and (not isinstance(row.reason, str) or len(row.reason) > 160)):
        raise invalid()
    filled = row.status == "filled"
    if filled:
        if row.filled_on is None or not row.submitted_on < row.filled_on <= account.current_date or row.filled_price is None or row.cash_delta is None:
            raise invalid()
    elif row.filled_on is not None or row.filled_price is not None or row.cash_delta is not None or row.fees != 0 or row.realized_pnl != 0:
        raise invalid()
    created = row.created_at
    if not isinstance(created, datetime) or (created.tzinfo is None and dialect != "sqlite"):
        raise invalid()
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return {"id": str(row.id), "code": row.code, "instrument_id": f"{row.code}.{'SH' if row.code.startswith('60') else 'SZ'}",
        "direction": row.direction, "quantity": row.quantity, "order_type": row.order_type,
        "limit_price": amount(row.limit_price, 4, nullable=True, positive=True), "status": row.status, "reason": row.reason,
        "submitted_on": row.submitted_on.isoformat(), "filled_on": row.filled_on.isoformat() if row.filled_on else None,
        "filled_price": amount(row.filled_price, 4, nullable=True, positive=True), "fees": amount(row.fees, 2, nonnegative=True),
        "cash_delta": amount(row.cash_delta, 2, nullable=True), "realized_pnl": amount(row.realized_pnl, 2),
        "created_at": created.astimezone(timezone.utc).isoformat(timespec="microseconds")}


async def read_order_history(factory, user_id: UUID, mode: str, account_id: UUID, query: ReplayHistoryQuery) -> dict:
    async with factory() as session, session.begin():
        dialect = session.get_bind().dialect.name
        if dialect not in ("postgresql", "sqlite"):
            raise ReplayError("history_backend_unsupported", "委托历史需要一致性事务支持", 503)
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ" if dialect == "postgresql" else "SERIALIZABLE"})
        if dialect == "postgresql":
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout='5s'"))
            await session.execute(text("SET LOCAL lock_timeout='2s'"))
            readonly = (await session.execute(text("SHOW transaction_read_only"))).scalar_one()
            isolation = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
            if readonly != "on" or isolation != "repeatable read":
                raise ReplayError("history_transaction_unsafe", "委托历史只读隔离未生效", 503)
        else:
            await session.execute(text("BEGIN"))
        account = (await session.execute(select(StockReplayAccount).where(StockReplayAccount.id == account_id,
            StockReplayAccount.user_id == user_id, StockReplayAccount.trading_mode == mode))).scalar_one_or_none()
        if account is None:
            raise ReplayError("account_not_found", "回放账户不存在", 404)
        if account.rules_version != RULES_VERSION or not account.start_date <= account.current_date <= account.end_date:
            raise ReplayError("unsupported_history_account", "账户方法或日期不受当前历史服务支持", 409)
        if account.revision != query.expected_revision:
            raise ReplayError("history_revision_conflict", "账户已推进或委托已变化，请先刷新账户再读取历史；未混用新旧分页", 409)
        bindings = account.datasets_json
        if not isinstance(bindings, dict) or not 1 <= len(bindings) <= 20 or any(not re.fullmatch(r"(?:00|60)[0-9]{4}", code) for code in bindings):
            raise invalid()
        try:
            dataset_bindings = [{"code": code, "dataset_id": str(UUID(value))} for code, value in sorted(bindings.items())]
        except (ValueError, TypeError, AttributeError):
            raise invalid() from None
        if query.code and query.code not in bindings:
            raise ReplayError("history_instrument_not_bound", "筛选证券不属于账户固定快照", 422)
        if any(value is not None and value > account.current_date for value in (query.start, query.end)):
            raise ReplayError("history_future_date", "筛选日期不得晚于当前回放游标", 422)
        filters = [StockReplayOrder.account_id == account.id]
        if query.status != "all":
            filters.append(StockReplayOrder.status == query.status)
        if query.direction != "all":
            filters.append(StockReplayOrder.direction == query.direction)
        if query.code:
            filters.append(StockReplayOrder.code == query.code)
        if query.start:
            filters.append(StockReplayOrder.submitted_on >= query.start)
        if query.end:
            filters.append(StockReplayOrder.submitted_on <= query.end)
        total = (await session.execute(select(func.count()).select_from(StockReplayOrder).where(*filters))).scalar_one()
        records = list((await session.execute(select(StockReplayOrder).where(*filters).order_by(StockReplayOrder.created_at.desc(), StockReplayOrder.id.desc())
            .offset(query.offset).limit(query.limit + 1))).scalars().all())
        items = [order_row(record, account, dialect) for record in records[:query.limit]]
        revision, cursor = account.revision, account.current_date.isoformat()
    return {"history_version": HISTORY_VERSION, "market": "stock", "account_id": str(account_id), "trading_mode": mode,
        "revision": revision, "current_date": cursor, "method_version": RULES_VERSION, "query": query.model_dump(mode="json"),
        "total": total, "items": items, "has_more": len(records) > query.limit, "dataset_bindings": dataset_bindings,
        "source": "internal_day_replay", "supplier": "internal_simulator", "currency": "CNY", "quantity_unit": "share", "price_unit": "CNY_per_share", "amount_unit": "CNY",
        "timezone": "Asia/Shanghai", "frequency": "order_event", "price_adjustment": "none", "license_scope": "account_owner_private",
        "trading_calendar": "account_bound_snapshot_not_revalidated_here", "financial_basis": "not_applicable", "collected_at": None,
        "data_version": None, "point_in_time_certified": False, "read_at": datetime.now(timezone.utc).isoformat(), "source_calls": 0, "model_calls": 0,
        "transaction": "postgresql_repeatable_read_readonly" if dialect == "postgresql" else "sqlite_explicit_select_only",
        "limitations": ["仅本人同登录盘已存日线模拟委托，不是券商成交或实盘收益认证。", "账户revision变动后必须刷新再查询，不是历史状态重建或不可变数据版本读取。", "快照ID仅表示账户原绑定，未在本接口重验来源、交易日历、公司行动或行情版本。"]}
