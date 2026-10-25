from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest

from app.services.stock_factors.engine import PreprocessConfig, preprocess


@pytest.mark.parametrize("count,constant_cap", [(2, False), (4, True)])
def test_unidentifiable_cap_neutralization_returns_missing(count, constant_cap):
    frame = pd.DataFrame([np.arange(count) + 1.0], columns=[str(index) for index in range(count)])
    cap = pd.DataFrame([[10.0 if constant_cap else 10.0 ** (index + 1) for index in range(count)]], columns=frame.columns)
    result = preprocess(frame, PreprocessConfig(winsorize="none", standardize=False, fillna=False, mktcap_neutral=True), mktcap=cap)
    assert result.isna().all().all()


def test_singleton_industry_does_not_become_zero_signal():
    frame = pd.DataFrame([[2.0, 3.0, 7.0]], columns=["one", "two", "three"])
    result = preprocess(frame, PreprocessConfig(winsorize="none", standardize=False, fillna=True, industry_neutral=True), industry={"one": "singleton", "two": "group", "three": "group"})
    assert pd.isna(result.loc[0, "one"])
    assert result.loc[0, ["two", "three"]].tolist() == [-2.0, 2.0]


@pytest.mark.asyncio
async def test_futures_cached_mark_handles_long_short_and_mode():
    from app.services.paper.valuation import cached_account_valuation
    from app.services.market_keys import quote_key

    positions = [SimpleNamespace(symbol="rb2701", direction="long", quantity=2, avg_price=Decimal("3000"), multiplier=10), SimpleNamespace(symbol="rb2701", direction="short", quantity=1, avg_price=Decimal("3050"), multiplier=10)]
    cache = AsyncMock()
    cache.mget.return_value = [b'{"symbol":"rb2701","source":"ctp:openctp","last_price":3020,"recv_ts":1000}']
    result = await cached_account_valuation(positions, Decimal("10000"), Decimal("2000"), cache, "virtual", now_ts=1010)
    assert result.equity == Decimal("12700.00")
    assert result.unrealized_pnl == Decimal("700.00")
    assert result.today_pnl is None
    cache.mget.assert_awaited_once_with([quote_key("virtual", "rb2701")])


@pytest.mark.asyncio
@pytest.mark.parametrize("quote", [None, b'{"symbol":"rb2701","source":"sina","last_price":3020,"recv_ts":1000}', b'{"symbol":"rb2701","source":"ctp:openctp","last_price":3020,"recv_ts":1}', b'{"symbol":"rb2701","source":"ctp:openctp:pulse","last_price":3020,"recv_ts":1000}', b'{"symbol":"au2701","source":"ctp:openctp","last_price":3020,"recv_ts":1000}'])
async def test_missing_or_incompatible_quote_never_fabricates_valuation(quote):
    from app.services.paper.valuation import cached_account_valuation

    position = SimpleNamespace(symbol="rb2701", direction="long", quantity=2, avg_price=Decimal("3000"), multiplier=10)
    cache = AsyncMock(); cache.mget.return_value = [quote]
    result = await cached_account_valuation([position], Decimal("10000"), Decimal("2000"), cache, "virtual", now_ts=1010)
    assert result.status == "unavailable"
    assert result.equity is None and result.unrealized_pnl is None and result.risk_rate is None


@pytest.mark.asyncio
@pytest.mark.parametrize("price,stamp", [(True, 1000), (3020, None), (3020, 1011), (float("nan"), 1000), (1e100, 1000)])
async def test_invalid_quote_numbers_fail_closed(price, stamp):
    import json
    from app.services.paper.valuation import cached_account_valuation

    cache = AsyncMock()
    cache.mget.return_value = [json.dumps({"symbol": "rb2701", "source": "ctp:openctp", "last_price": price, "recv_ts": stamp})]
    position = SimpleNamespace(symbol="rb2701", direction="long", quantity=1, avg_price=Decimal("3000"), multiplier=10)
    result = await cached_account_valuation([position], Decimal("100"), Decimal("20"), cache, "virtual", now_ts=1010)
    assert result.status == "unavailable"
    assert result.equity is None and result.unrealized_pnl is None


@pytest.mark.asyncio
async def test_empty_positions_have_exact_zero_floating_not_invented_daily_pnl():
    from app.services.paper.valuation import cached_account_valuation

    cache = AsyncMock()
    result = await cached_account_valuation([], Decimal("100.01"), Decimal("20.02"), cache, "virtual", now_ts=1010)
    assert result.status == "no_positions"
    assert result.equity == Decimal("120.03") and result.unrealized_pnl == 0
    assert result.today_pnl is None
    cache.mget.assert_not_awaited()


@pytest.mark.asyncio
async def test_negative_equity_is_not_clamped_or_given_finite_risk_ratio():
    from app.services.paper.valuation import cached_account_valuation

    cache = AsyncMock()
    cache.mget.return_value = [b'{"symbol":"rb2701","source":"ctp:openctp","last_price":2900,"recv_ts":1000}']
    position = SimpleNamespace(symbol="rb2701", direction="long", quantity=1, avg_price=Decimal("3000"), multiplier=10)
    result = await cached_account_valuation([position], Decimal("100.01"), Decimal("20.02"), cache, "virtual", now_ts=1010)
    assert result.equity == Decimal("-879.97") and result.risk_rate is None


@pytest.mark.asyncio
async def test_cache_failure_has_no_retry_or_cross_mode_fallback():
    from app.services.paper.valuation import cached_account_valuation

    cache = AsyncMock()
    cache.mget.side_effect = ConnectionError("synthetic cache failure")
    position = SimpleNamespace(symbol="rb2701", direction="long", quantity=1, avg_price=Decimal("3000"), multiplier=10)
    result = await cached_account_valuation([position], Decimal("100"), Decimal("20"), cache, "virtual", now_ts=1010)
    assert result.issues == ["quote_cache_read_failed"] and result.equity is None
    assert cache.mget.await_count == 1
