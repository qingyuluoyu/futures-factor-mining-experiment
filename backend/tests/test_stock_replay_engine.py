from datetime import date
from decimal import Decimal

import pytest

from app.services.stock_replay_engine import (
    ReplayBar,
    ReplayError,
    ReplayInstruction,
    ReplayLot,
    ReplaySettings,
    calculate_fees,
    check_transition,
    equity_value,
    match_order,
    validate_order,
)


def raw_bar(day="2026-09-02", **changes):
    return ReplayBar.model_validate({
        "trade_date": day, "open": "10", "high": "12", "low": "9",
        "close": "11", "volume_shares": 100000, "adj_factor": "100",
        "up_limit": "12", "down_limit": "8", **changes,
    })


def instruction(direction="buy", **changes):
    return ReplayInstruction.model_validate({
        "code": "000001", "direction": direction, "quantity": 100,
        "order_type": "market", "submitted_on": "2026-09-01", **changes,
    })


SETTINGS = ReplaySettings(slippage_rate=Decimal("0"))


def test_buy_then_sell_reconciles_cash_fees_equity_and_profit():
    bought = match_order(instruction(), raw_bar(), [], Decimal("10000"), SETTINGS)
    assert bought.status == "filled"
    assert bought.price == Decimal("10.00")
    assert bought.fees == Decimal("5.01")
    assert bought.cash == Decimal("8994.99")
    assert equity_value(bought.cash, bought.lots, {"000001": raw_bar()}) == Decimal("10094.99")
    sold = match_order(
        instruction("sell", submitted_on="2026-09-02"),
        raw_bar("2026-09-03", open="11"), bought.lots, bought.cash, SETTINGS,
    )
    assert sold.status == "filled"
    assert sold.lots == []
    assert sold.fees == Decimal("5.56")
    assert sold.cash == Decimal("10089.43")
    assert sold.realized_pnl == Decimal("89.43")


@pytest.mark.parametrize("direction,quantity,code", [("buy", 99, "000001"), ("sell", 101, "000001"), ("buy", 100, "rb2701"), ("buy", 200, "688001")])
def test_invalid_or_unsupported_stock_orders_are_rejected(direction, quantity, code):
    with pytest.raises(ReplayError):
        validate_order(instruction(direction, quantity=quantity, code=code))


def test_same_day_execution_is_forbidden():
    with pytest.raises(ReplayError, match="下一"):
        match_order(instruction(submitted_on="2026-09-02"), raw_bar(), [], Decimal("10000"), SETTINGS)


def test_t_plus_one_and_no_short_selling():
    today_lot = ReplayLot(code="000001", quantity=100, cost_per_share=Decimal("10"), acquired_on=date(2026, 9, 2))
    result = match_order(instruction("sell"), raw_bar(), [today_lot], Decimal("0"), SETTINGS)
    assert result.status == "rejected"
    assert result.reason == "t_plus_one_or_insufficient_position"
    assert result.lots == [today_lot]
    empty = match_order(instruction("sell"), raw_bar(), [], Decimal("10000"), SETTINGS)
    assert empty.status == "rejected"


@pytest.mark.parametrize("direction,opening,reason", [("buy", "12", "limit_up"), ("sell", "8", "limit_down")])
def test_locked_opening_prices_do_not_invent_fills(direction, opening, reason):
    result = match_order(instruction(direction), raw_bar(open=opening, low=str(min(Decimal(opening), Decimal("9")))), [], Decimal("10000"), SETTINGS)
    assert result.status == "rejected"
    assert result.reason == reason


def test_limit_order_does_not_use_later_intraday_low_to_fill():
    result = match_order(instruction(order_type="limit", limit_price="9.50"), raw_bar(), [], Decimal("10000"), SETTINGS)
    assert result.status == "pending"
    assert result.cash == Decimal("10000")
    assert result.fees == 0


def test_insufficient_cash_and_volume_never_create_partial_or_negative_positions():
    result = match_order(instruction(), raw_bar(), [], Decimal("1000"), SETTINGS)
    assert result.status == "rejected"
    assert result.cash == Decimal("1000")
    low_volume = match_order(instruction(), raw_bar(volume_shares=500), [], Decimal("10000"), SETTINGS)
    assert low_volume.status == "rejected"
    assert low_volume.reason == "liquidity_limit"


def test_other_pending_cash_reservations_are_respected():
    result = match_order(instruction(), raw_bar(), [], Decimal("10000"), SETTINGS, spendable_cash=Decimal("1000"))
    assert result.status == "rejected"


def test_slippage_never_breaks_a_buy_limit():
    result = match_order(instruction(order_type="limit", limit_price="10.01"), raw_bar(), [], Decimal("10000"), ReplaySettings(slippage_rate=Decimal("0.01")))
    assert result.price == Decimal("10.01")


def test_corporate_action_and_missing_valuation_fail_closed():
    with pytest.raises(ReplayError, match="公司行动"):
        check_transition(raw_bar("2026-09-01"), raw_bar(adj_factor="101"))
    with pytest.raises(ReplayError):
        equity_value(Decimal("1000"), [ReplayLot(code="000001", quantity=100, cost_per_share=Decimal("10"), acquired_on=date(2026, 9, 1))], {})


def test_tax_rates_follow_execution_date():
    assert calculate_fees(Decimal("1000"), "sell", date(2023, 8, 25), SETTINGS) == Decimal("6.01")
    assert calculate_fees(Decimal("1000"), "sell", date(2023, 8, 28), SETTINGS) == Decimal("5.51")
    assert calculate_fees(Decimal("1000"), "sell", date(2022, 4, 28), SETTINGS) == Decimal("6.02")


def test_sell_fees_cannot_overdraw_cash_or_consume_other_reservations():
    lot = ReplayLot(code="000001", quantity=100, cost_per_share=Decimal(".5"), acquired_on=date(2026, 9, 1))
    bar = raw_bar(open=".50", high=".55", low=".45", close=".50", up_limit=".55", down_limit=".45")
    settings = ReplaySettings(min_commission=100, slippage_rate=0)
    for cash, available in [(Decimal("0"), Decimal("0")), (Decimal("100"), Decimal("0"))]:
        result = match_order(instruction("sell"), bar, [lot], cash, settings, spendable_cash=available)
        assert result.status == "rejected" and result.reason == "insufficient_cash_for_fees"
        assert result.cash == cash and result.lots == [lot]


def test_partial_fifo_sales_carry_cost_rounding_remainder_until_final_liquidation():
    result = match_order(instruction(quantity=300), raw_bar(), [], Decimal("10000"), SETTINGS)
    realized = Decimal("0")
    for unused in range(3):
        result = match_order(instruction("sell", submitted_on="2026-09-02"), raw_bar("2026-09-03", open="11"), result.lots, result.cash, SETTINGS)
        assert result.status == "filled"
        realized += result.realized_pnl
    assert result.lots == []
    assert result.cash - Decimal("10000") == realized == Decimal("278.29")
