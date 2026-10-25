from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services.kline_history import (
    get_latest_history_date,
    list_mining_symbols,
    query_history_bars,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["bars", "latest", "mining"])
async def test_futures_history_queries_always_scope_market(query):
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.scalar_one_or_none.return_value = None
    result.all.return_value = []
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    if query == "bars":
        await query_history_bars(session, "rb", "2026-10-17")
    elif query == "latest":
        await get_latest_history_date(session, "rb")
    else:
        await list_mining_symbols(session)
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "kline_history.market_type =" in str(compiled)
    assert "futures" in compiled.params.values()
