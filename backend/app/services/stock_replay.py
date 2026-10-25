import hashlib
import json
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock_replay import StockReplayAccount, StockReplayDataset, StockReplayOrder, StockReplayStep
from app.models.user import User
from app.schemas.stock_replay import CreateReplayAccount, ReplayMutation, SubmitReplayOrder
from app.services.stock_replay_engine import (
    RULES_VERSION, ReplayBar, ReplayError, ReplayInstruction, ReplayLot, ReplaySettings,
    calculate_fees, check_transition, equity_value, match_order, money,
)


def request_hash(value: dict) -> str:
    def normalize(item):
        if isinstance(item, Decimal):
            return format(item.normalize(), "f")
        if isinstance(item, (UUID, date)):
            return str(item)
        if isinstance(item, dict):
            return {key: normalize(entry) for key, entry in item.items()}
        if isinstance(item, list):
            return [normalize(entry) for entry in item]
        return item
    return hashlib.sha256(json.dumps(normalize(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def snapshot_bars(rows: list, day: date) -> ReplayBar:
    found = [row for row in rows if row.get("trade_date") == day.isoformat()]
    if len(found) != 1:
        raise ReplayError("missing_bar", "快照缺少唯一的当日原始行情，停止回放而不使用旧价", 409)
    return ReplayBar.model_validate(found[0])


class StockReplayService:
    def __init__(self, session: AsyncSession, user_id: UUID, trading_mode: str):
        self.session = session
        self.user_id = user_id
        self.mode = trading_mode

    async def account(self, account_id: UUID, *, lock: bool = False) -> StockReplayAccount:
        statement = select(StockReplayAccount).where(StockReplayAccount.id == account_id, StockReplayAccount.user_id == self.user_id, StockReplayAccount.trading_mode == self.mode)
        if lock:
            statement = statement.with_for_update()
        account = (await self.session.execute(statement)).scalar_one_or_none()
        if account is None:
            raise ReplayError("account_not_found", "回放账户不存在", 404)
        if account.rules_version != RULES_VERSION:
            raise ReplayError("unsupported_method", "账户撮合方法版本不受当前服务支持", 409)
        return account

    async def datasets(self, account: StockReplayAccount) -> dict[str, StockReplayDataset]:
        ids = [UUID(value) for value in account.datasets_json.values()]
        rows = (await self.session.execute(select(StockReplayDataset).where(StockReplayDataset.id.in_(ids)).limit(20))).scalars().all()
        result = {row.code: row for row in rows}
        if len(result) != len(ids) or any(str(result[code].id) != value for code, value in account.datasets_json.items()):
            raise ReplayError("missing_dataset", "账户绑定的数据版本缺失", 409)
        return result

    async def pending(self, account_id: UUID) -> list[StockReplayOrder]:
        rows = (await self.session.execute(select(StockReplayOrder).where(StockReplayOrder.account_id == account_id, StockReplayOrder.status == "pending").order_by(StockReplayOrder.created_at, StockReplayOrder.id).limit(101))).scalars().all()
        if len(rows) > 100:
            raise ReplayError("pending_limit", "待处理委托超过安全上限", 409)
        return list(rows)

    async def list_accounts(self, limit: int, offset: int) -> list[dict]:
        rows = (await self.session.execute(select(StockReplayAccount).where(StockReplayAccount.user_id == self.user_id, StockReplayAccount.trading_mode == self.mode).order_by(StockReplayAccount.created_at.desc(), StockReplayAccount.id).offset(offset).limit(limit))).scalars().all()
        return [{"id": str(row.id), "name": row.name, "current_date": row.current_date.isoformat(), "status": row.status, "revision": row.revision} for row in rows]

    async def create(self, body: CreateReplayAccount) -> dict:
        await self.session.execute(select(User.id).where(User.id == self.user_id).with_for_update())
        digest = request_hash(body.model_dump(exclude={"idempotency_key"}))
        existing = (await self.session.execute(select(StockReplayAccount).where(StockReplayAccount.user_id == self.user_id, StockReplayAccount.trading_mode == self.mode, StockReplayAccount.create_key == body.idempotency_key).with_for_update())).scalar_one_or_none()
        if existing:
            self.check_hash(existing.params_hash, digest)
            return await self.view(existing)
        count = await self.session.scalar(select(func.count()).select_from(StockReplayAccount).where(StockReplayAccount.user_id == self.user_id, StockReplayAccount.trading_mode == self.mode))
        if count >= 1000:
            raise ReplayError("account_limit", "当前用户每个盘最多保存1000个回放账户", 409)
        datasets = (await self.session.execute(select(StockReplayDataset).where(StockReplayDataset.id.in_(body.dataset_ids)).limit(20))).scalars().all()
        if len(datasets) != len(body.dataset_ids) or len({row.code for row in datasets}) != len(datasets):
            raise ReplayError("invalid_datasets", "快照不存在或同一证券选择了多个版本")
        for dataset in datasets:
            if not dataset.start_date <= body.start_date <= dataset.end_date or body.start_date.isoformat() not in dataset.meta["calendar_dates"]:
                raise ReplayError("invalid_start_date", "起始日期必须是所有快照共同覆盖的交易日")
            snapshot_bars(dataset.bars_json, body.start_date)
        end_date = min(row.end_date for row in datasets)
        settings = ReplaySettings(commission_rate=body.commission_rate, min_commission=body.min_commission, slippage_rate=body.slippage_rate)
        account = StockReplayAccount(user_id=self.user_id, trading_mode=self.mode, name=body.name, initial_cash=body.initial_cash, cash=body.initial_cash, start_date=body.start_date, current_date=body.start_date, end_date=end_date, datasets_json={row.code: str(row.id) for row in datasets}, positions_json=[], settings_json=settings.model_dump(mode="json"), status="completed" if body.start_date == end_date else "active", create_key=body.idempotency_key, params_hash=digest, rules_version=RULES_VERSION)
        self.session.add(account)
        await self.session.flush()
        return await self.view(account)

    @staticmethod
    def check_hash(existing: str, digest: str) -> None:
        if existing != digest:
            raise ReplayError("idempotency_conflict", "同一幂等键不能用于不同请求", 409)

    @staticmethod
    def check_revision(account: StockReplayAccount, expected: int) -> None:
        if account.revision != expected:
            raise ReplayError("revision_conflict", "账户已变化，请刷新后重新操作", 409)
        if account.status != "active":
            raise ReplayError("replay_completed", "回放已结束，请新建账户开始另一段实验", 409)

    async def check_step_key(self, account_id: UUID, key: UUID, digest: str) -> dict | None:
        existing = (await self.session.execute(select(StockReplayStep).where(StockReplayStep.account_id == account_id, StockReplayStep.idempotency_key == key))).scalar_one_or_none()
        if existing:
            self.check_hash(existing.params_hash, digest)
            return existing.result_json
        order = (await self.session.execute(select(StockReplayOrder.id).where(StockReplayOrder.account_id == account_id, StockReplayOrder.idempotency_key == key))).scalar_one_or_none()
        if order:
            raise ReplayError("idempotency_conflict", "幂等键已用于另一种操作", 409)
        return None

    async def submit(self, account_id: UUID, body: SubmitReplayOrder) -> dict:
        account = await self.account(account_id, lock=True)
        digest = request_hash({"operation": "order", **body.model_dump(exclude={"idempotency_key"})})
        existing = (await self.session.execute(select(StockReplayOrder).where(StockReplayOrder.account_id == account.id, StockReplayOrder.idempotency_key == body.idempotency_key))).scalar_one_or_none()
        if existing:
            self.check_hash(existing.params_hash, digest)
            return await self.view(account, body.code)
        await self.check_step_key(account.id, body.idempotency_key, digest)
        self.check_revision(account, body.expected_revision)
        datasets = await self.datasets(account)
        if body.code not in datasets:
            raise ReplayError("instrument_not_in_account", "证券不在该账户绑定的数据快照中")
        pending = await self.pending(account.id)
        if len(pending) >= 100:
            raise ReplayError("pending_limit", "账户最多100笔待处理委托", 409)
        settings = ReplaySettings.model_validate(account.settings_json)
        reserved = Decimal("0")
        if body.direction == "buy":
            bar = snapshot_bars(datasets[body.code].bars_json, account.current_date)
            estimate = body.limit_price if body.limit_price is not None else money(bar.close * (1 + settings.slippage_rate))
            notional = money(estimate * body.quantity)
            reserved = notional + calculate_fees(notional, "buy", account.current_date, settings)
            available = account.cash - sum((order.reserved_cash for order in pending), Decimal("0"))
            if reserved > available:
                raise ReplayError("insufficient_cash", "可用模拟资金不足（已考虑其他待买委托冻结）", 409)
        else:
            quantity = sum(lot["quantity"] for lot in account.positions_json if lot["code"] == body.code)
            locked = sum(order.quantity for order in pending if order.code == body.code and order.direction == "sell")
            if body.quantity > quantity - locked:
                raise ReplayError("insufficient_position", "次日可卖持仓不足，不支持卖空", 409)
        self.session.add(StockReplayOrder(account_id=account.id, idempotency_key=body.idempotency_key, params_hash=digest, code=body.code, direction=body.direction, quantity=body.quantity, order_type=body.order_type, limit_price=body.limit_price, submitted_on=account.current_date, reserved_cash=reserved))
        account.revision += 1
        await self.session.flush()
        return await self.view(account, body.code)

    async def mutate(self, account_id: UUID, body: ReplayMutation, *, cancel_id: UUID | None = None) -> dict:
        account = await self.account(account_id, lock=True)
        kind = "cancel" if cancel_id else "advance"
        digest = request_hash({"operation": kind, "order_id": cancel_id, **body.model_dump(exclude={"idempotency_key"})})
        cached = await self.check_step_key(account.id, body.idempotency_key, digest)
        if cached is not None:
            return cached
        self.check_revision(account, body.expected_revision)
        pending = await self.pending(account.id)
        if cancel_id:
            order = next((order for order in pending if order.id == cancel_id), None)
            if order is None:
                raise ReplayError("order_not_pending", "委托不存在或不处于待处理状态", 409)
            order.status, order.reason, order.reserved_cash = "cancelled", "user_cancelled", Decimal("0")
        else:
            datasets = await self.datasets(account)
            calendars = [dataset.meta["calendar_dates"] for dataset in datasets.values()]
            future_dates = [date.fromisoformat(value) for value in calendars[0] if account.current_date.isoformat() < value <= account.end_date.isoformat()]
            if not future_dates:
                raise ReplayError("missing_calendar", "快照缺少下一交易日，不能越界推进", 409)
            next_date = min(future_dates)
            if any(next_date.isoformat() not in calendar for calendar in calendars):
                raise ReplayError("calendar_conflict", "数据快照交易日历冲突，已停止推进", 409)
            next_bars = {}
            for code, dataset in datasets.items():
                before = snapshot_bars(dataset.bars_json, account.current_date)
                after = snapshot_bars(dataset.bars_json, next_date)
                check_transition(before, after)
                next_bars[code] = after
            lots = [ReplayLot.model_validate(lot) for lot in account.positions_json]
            settings = ReplaySettings.model_validate(account.settings_json)
            used_volume: dict[str, int] = {}
            for order in pending:
                other_reservations = sum((other.reserved_cash for other in pending if other.id != order.id and other.status == "pending"), Decimal("0"))
                instruction = ReplayInstruction(code=order.code, direction=order.direction, quantity=order.quantity, order_type=order.order_type, limit_price=order.limit_price, submitted_on=order.submitted_on)
                fill = match_order(instruction, next_bars[order.code], lots, account.cash, settings, spendable_cash=max(Decimal("0"), account.cash - other_reservations), used_volume=used_volume.get(order.code, 0))
                order.status, order.reason = fill.status, fill.reason
                if fill.status != "pending":
                    order.reserved_cash = Decimal("0")
                if fill.status == "filled":
                    order.filled_on, order.filled_price = next_date, fill.price
                    order.fees, order.cash_delta, order.realized_pnl = fill.fees, fill.cash_delta, fill.realized_pnl
                    account.cash = fill.cash
                    account.fees_paid += fill.fees
                    account.realized_pnl += fill.realized_pnl
                    lots = fill.lots
                    used_volume[order.code] = used_volume.get(order.code, 0) + order.quantity
            account.positions_json = [lot.model_dump(mode="json") for lot in lots]
            account.current_date = next_date
            if next_date == account.end_date:
                account.status = "completed"
                for order in pending:
                    if order.status == "pending":
                        order.status, order.reason, order.reserved_cash = "cancelled", "end_of_replay", Decimal("0")
        previous_revision = account.revision
        account.revision += 1
        await self.session.flush()
        result = await self.view(account)
        self.session.add(StockReplayStep(account_id=account.id, idempotency_key=body.idempotency_key, kind=kind, from_revision=previous_revision, params_hash=digest, result_json=result))
        await self.session.flush()
        return result

    async def view(self, account: StockReplayAccount, code: str | None = None) -> dict:
        datasets = await self.datasets(account)
        code = code or sorted(datasets)[0]
        if code not in datasets:
            raise ReplayError("instrument_not_in_account", "该证券不属于回放账户")
        bars = {key: snapshot_bars(dataset.bars_json, account.current_date) for key, dataset in datasets.items()}
        lots = [ReplayLot.model_validate(lot) for lot in account.positions_json]
        pending = await self.pending(account.id)
        reserved = sum((order.reserved_cash for order in pending), Decimal("0"))
        equity = equity_value(account.cash, lots, bars)
        positions = []
        for key in sorted({lot.code for lot in lots}):
            selected = [lot for lot in lots if lot.code == key]
            quantity = sum(lot.quantity for lot in selected)
            cost = sum((lot.cost_per_share * lot.quantity for lot in selected), Decimal("0"))
            market_value = money(bars[key].close * quantity)
            positions.append({"code": key, "name": datasets[key].name, "instrument_id": datasets[key].instrument_id, "quantity": quantity, "available_quantity": quantity - sum(order.quantity for order in pending if order.code == key and order.direction == "sell"), "cost_price": format(cost / quantity, ".4f"), "last_price": str(bars[key].close), "market_value": str(market_value), "unrealized_pnl": str(money(market_value - cost))})
        history = (await self.session.execute(select(StockReplayOrder).where(StockReplayOrder.account_id == account.id, StockReplayOrder.status != "pending").order_by(StockReplayOrder.created_at.desc(), StockReplayOrder.id).limit(200))).scalars().all()
        orders = sorted([*pending, *history], key=lambda order: (order.created_at, str(order.id)), reverse=True)
        chart = [{"time": row["trade_date"], **{field: float(row[field]) for field in ("open", "high", "low", "close")}, "volume": row["volume_shares"]} for row in datasets[code].bars_json if row["trade_date"] <= account.current_date.isoformat()][-250:]
        return {
            "id": str(account.id), "name": account.name, "market_type": "stock", "trading_mode": account.trading_mode, "method_version": account.rules_version, "price_adjustment": "none", "currency": "CNY",
            "initial_cash": str(money(account.initial_cash)), "cash": str(money(account.cash)), "available_cash": str(money(account.cash - reserved)), "reserved_cash": str(money(reserved)), "equity": str(equity), "total_pnl": str(money(equity - account.initial_cash)), "fees_paid": str(money(account.fees_paid)), "realized_pnl": str(money(account.realized_pnl)),
            "current_date": account.current_date.isoformat(), "start_date": account.start_date.isoformat(), "end_date": account.end_date.isoformat(), "status": account.status, "revision": account.revision, "positions": positions,
            "orders": [{"id": str(order.id), "code": order.code, "direction": order.direction, "quantity": order.quantity, "order_type": order.order_type, "limit_price": str(order.limit_price) if order.limit_price is not None else None, "status": order.status, "reason": order.reason, "submitted_on": order.submitted_on.isoformat(), "filled_on": order.filled_on.isoformat() if order.filled_on else None, "filled_price": str(order.filled_price) if order.filled_price is not None else None, "fees": str(money(order.fees)), "cash_delta": str(money(order.cash_delta)) if order.cash_delta is not None else None, "realized_pnl": str(money(order.realized_pnl))} for order in orders],
            "instruments": [{"code": dataset.code, "name": dataset.name, "instrument_id": dataset.instrument_id} for dataset in sorted(datasets.values(), key=lambda item: item.code)],
            "chart": {"code": code, "adjustment": "none", "volume_unit": "share", "bars": chart}, "data_versions": sorted(dataset.version for dataset in datasets.values()),
        }
