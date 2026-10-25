from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from pydantic import BaseModel, Field, model_validator


RULES_VERSION = "stock-replay-v1"
CENT = Decimal("0.01")


class ReplayError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class ReplaySettings(BaseModel):
    commission_rate: Decimal = Field(default=Decimal("0.0003"), ge=0, le=Decimal("0.01"))
    min_commission: Decimal = Field(default=Decimal("5"), ge=0, le=100)
    slippage_rate: Decimal = Field(default=Decimal("0.001"), ge=0, le=Decimal("0.02"))
    participation_rate: Decimal = Field(default=Decimal("0.1"), gt=0, le=Decimal("0.1"))


class ReplayBar(BaseModel):
    trade_date: date
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume_shares: int = Field(ge=0)
    adj_factor: Decimal = Field(gt=0)
    up_limit: Decimal = Field(gt=0)
    down_limit: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def consistent_prices(self):
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close) or self.high < self.low:
            raise ValueError("原始日线 OHLC 不一致")
        if self.up_limit <= self.down_limit:
            raise ValueError("涨跌停价格不一致")
        return self


class ReplayLot(BaseModel):
    code: str
    quantity: int = Field(gt=0)
    cost_per_share: Decimal = Field(ge=0)
    acquired_on: date


class ReplayInstruction(BaseModel):
    code: str
    direction: Literal["buy", "sell"]
    quantity: int = Field(gt=0, le=1000000)
    order_type: Literal["market", "limit"] = "market"
    limit_price: Decimal | None = Field(default=None, gt=0)
    submitted_on: date


class ReplayFill(BaseModel):
    status: Literal["pending", "filled", "rejected"]
    reason: str | None = None
    price: Decimal | None = None
    fees: Decimal = Decimal("0")
    cash: Decimal
    cash_delta: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    lots: list[ReplayLot]


def validate_order(order: ReplayInstruction) -> None:
    import re

    if not re.fullmatch(r"(?:60|00)[0-9]{4}", order.code):
        raise ReplayError("unsupported_instrument", "当前回放仅支持沪深主板普通 A 股，不支持期货、科创板、创业板或北交所")
    if order.quantity % 100 != 0:
        raise ReplayError("invalid_lot_size", "当前主板回放买卖数量必须为100股的整数倍")
    if order.order_type == "limit" and order.limit_price is None:
        raise ReplayError("missing_limit_price", "限价委托必须填写限价")
    if order.order_type == "market" and order.limit_price is not None:
        raise ReplayError("unexpected_limit_price", "市价委托不应传入限价")


def calculate_fees(notional: Decimal, direction: str, execution_date: date, settings: ReplaySettings) -> Decimal:
    if execution_date < date(2018, 1, 1):
        raise ReplayError("unsupported_fee_period", "当前费用方法支持2018年起的回放区间")
    commission = money(max(settings.min_commission, notional * settings.commission_rate))
    transfer_rate = Decimal("0.00001") if execution_date >= date(2022, 4, 29) else Decimal("0.00002")
    stamp_rate = Decimal("0.0005") if execution_date >= date(2023, 8, 28) else Decimal("0.001")
    transfer_fee = money(notional * transfer_rate)
    stamp_tax = money(notional * stamp_rate) if direction == "sell" else Decimal("0")
    return commission + transfer_fee + stamp_tax


def check_transition(previous: ReplayBar, following: ReplayBar) -> None:
    if following.trade_date <= previous.trade_date:
        raise ReplayError("invalid_date_transition", "回放必须推进到下一交易日")
    if previous.adj_factor != following.adj_factor:
        raise ReplayError("corporate_action_unhandled", "该交易日存在复权因子变化；公司行动尚未支持，已停止推进并保留账户状态", 409)


def equity_value(cash: Decimal, lots: list[ReplayLot], bars: dict[str, ReplayBar]) -> Decimal:
    for lot in lots:
        if lot.code not in bars:
            raise ReplayError("missing_valuation", "持仓缺少当日原始收盘价，不能以零或过期价格估值", 409)
    return money(cash + sum((bars[lot.code].close * lot.quantity for lot in lots), Decimal("0")))


def match_order(
    order: ReplayInstruction,
    bar: ReplayBar,
    lots: list[ReplayLot],
    cash: Decimal,
    settings: ReplaySettings,
    *,
    spendable_cash: Decimal | None = None,
    used_volume: int = 0,
) -> ReplayFill:
    validate_order(order)
    if order.submitted_on >= bar.trade_date:
        raise ReplayError("same_day_execution", "收盘后提交的委托只能在下一交易日开盘撮合")
    if cash < 0:
        raise ReplayError("invalid_account_cash", "账户现金异常，已停止撮合", 409)
    current_lots = [lot.model_copy() for lot in lots]

    def unchanged(status: Literal["pending", "rejected"], reason: str) -> ReplayFill:
        return ReplayFill(status=status, reason=reason, cash=cash, lots=current_lots)

    if order.direction == "buy" and bar.open >= bar.up_limit:
        return unchanged("rejected", "limit_up")
    if order.direction == "sell" and bar.open <= bar.down_limit:
        return unchanged("rejected", "limit_down")
    if order.order_type == "limit":
        if order.direction == "buy" and bar.open > order.limit_price:
            return unchanged("pending", "limit_not_crossed_at_open")
        if order.direction == "sell" and bar.open < order.limit_price:
            return unchanged("pending", "limit_not_crossed_at_open")
    volume_limit = int(Decimal(bar.volume_shares) * settings.participation_rate)
    if order.quantity + used_volume > volume_limit:
        return unchanged("rejected", "liquidity_limit")
    rate = Decimal("1") + settings.slippage_rate if order.direction == "buy" else Decimal("1") - settings.slippage_rate
    price = money(bar.open * rate)
    price = min(price, bar.up_limit) if order.direction == "buy" else max(price, bar.down_limit)
    if order.limit_price is not None:
        price = min(price, order.limit_price) if order.direction == "buy" else max(price, order.limit_price)
    notional = money(price * order.quantity)
    fees = calculate_fees(notional, order.direction, bar.trade_date, settings)
    if order.direction == "buy":
        required = notional + fees
        if required > cash or required > (cash if spendable_cash is None else spendable_cash):
            return unchanged("rejected", "insufficient_cash")
        current_lots.append(ReplayLot(code=order.code, quantity=order.quantity, cost_per_share=required / order.quantity, acquired_on=bar.trade_date))
        return ReplayFill(status="filled", price=price, fees=fees, cash=money(cash - required), cash_delta=-required, lots=current_lots)
    sellable = sum(lot.quantity for lot in current_lots if lot.code == order.code and lot.acquired_on < bar.trade_date)
    if order.quantity > sellable:
        return unchanged("rejected", "t_plus_one_or_insufficient_position")
    proceeds = notional - fees
    if cash + proceeds < 0 or (proceeds < 0 and -proceeds > (cash if spendable_cash is None else spendable_cash)):
        return unchanged("rejected", "insufficient_cash_for_fees")
    remaining = order.quantity
    cost_basis = Decimal("0")
    for lot in sorted(current_lots, key=lambda item: item.acquired_on):
        if lot.code != order.code or lot.acquired_on >= bar.trade_date:
            continue
        consumed = min(remaining, lot.quantity)
        lot_cost = money(lot.quantity * lot.cost_per_share)
        consumed_cost = lot_cost if consumed == lot.quantity else money(lot_cost * consumed / lot.quantity)
        cost_basis += consumed_cost
        lot.quantity -= consumed
        if lot.quantity:
            lot.cost_per_share = (lot_cost - consumed_cost) / lot.quantity
        remaining -= consumed
        if remaining == 0:
            break
    return ReplayFill(
        status="filled", price=price, fees=fees, cash=money(cash + proceeds), cash_delta=proceeds,
        realized_pnl=money(proceeds - cost_basis), lots=[lot for lot in current_lots if lot.quantity > 0],
    )
