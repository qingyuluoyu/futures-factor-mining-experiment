"""回测虚拟账户与指标"""

from app.services.backtest.account import VirtualAccount
from app.services.backtest.metrics import compute_metrics


def test_open_close_long_profit():
    acc = VirtualAccount(
        cash=1_000_000,
        multiplier=10,
        margin_rate=0.1,
        fee_mode="fixed",
        open_fee=5,
        close_fee=5,
    )
    acc.apply("open_long", 2, 100, "2026-01-01", "开多")
    assert acc.side == "long"
    assert acc.qty == 2
    acc.apply("close", 2, 110, "2026-01-02", "平多")
    assert acc.side == "flat"
    # 盈亏 (110-100)*2*10 = 200；开仓费 5*2 + 平仓费 5*2 = 20
    assert acc.realized_pnl == 200
    assert acc.cash == 1_000_000 + 200 - 20


def test_metrics_basic():
    curve = [
        {"time": "a", "equity": 100},
        {"time": "b", "equity": 120},
        {"time": "c", "equity": 90},
    ]
    trades = [
        {"action": "close", "pnl": 20},
        {"action": "close", "pnl": -10},
    ]
    m = compute_metrics(curve, trades, 100, 90, 10, 1)
    assert m["max_drawdown"] == 30
    assert m["win_count"] == 1
    assert m["loss_count"] == 1
