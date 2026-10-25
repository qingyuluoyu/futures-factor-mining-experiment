from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.kline_history import KlineHistory
from app.services.kline_history import list_mining_symbols


@pytest.mark.asyncio
async def test_stock_prices_mislabeled_as_futures_never_become_mining_candidates():
    engine = create_engine("sqlite:///:memory:")
    KlineHistory.__table__.create(engine)
    try:
        with Session(engine) as session:
            for index, (market, code) in enumerate((("futures", "RB"), ("futures", "RB"), ("futures", "300308.SZ"), ("futures", "UNKNOWN"), ("stock", "600519"))):
                session.add(KlineHistory(market_type=market, code=code, trade_date=date(2026, 10, index + 1), open=100, high=101, low=99, close=100, volume=1000, hold=0))
            session.commit()

            class DatabaseSession:
                async def execute(self, statement):
                    return session.execute(statement)

            items = await list_mining_symbols(DatabaseSession())
            assert [item["code"] for item in items] == ["RB"]
            assert items[0]["bars"] == 2
            assert len(session.scalars(select(KlineHistory)).all()) == 5
    finally:
        engine.dispose()
