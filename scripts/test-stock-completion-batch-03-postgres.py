import asyncio
from datetime import date
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pandas as pd
import redis.asyncio as aioredis
from pydantic import TypeAdapter
from sqlalchemy import update


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api import stock_research_jobs as job_api
from app.core.deps import get_settings
from app.models.stock_research_job import StockResearchSchemaVersion
from app.models.user import User
from app.schemas.stock_research_jobs import CreateResearchJob
from app.services.ai import gateway
from app.services.ai.providers.base import StreamEvent
from app.services.kline_keys import history_kline_key
from app.services.market_keys import quote_key
from app.services.stock_factors.engine import BacktestParams, METHOD_VERSION, MarketData, run_portfolio_backtest
from app.services.stock_research_jobs import ResearchJobService, claim_next_job, finish_job


def read_tool_result(response):
    for block in response.text.split("\n\n"):
        if block.startswith("event: tool_result"):
            data = json.loads(next(line[6:] for line in block.splitlines() if line.startswith("data: ")))
            return json.loads(data["content"])
    raise AssertionError("Missing tool result")


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    if settings.redis_host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Only local Redis QA keys permitted")
    symbol = "qa_b03_" + uuid4().hex
    if not re.fullmatch("qa_b03_[a-f0-9]{32}", symbol):
        raise ValueError("Invalid QA symbol")
    redis_conn = aioredis.Redis(host=settings.redis_host, port=settings.redis_port, db=settings.redis_db, socket_timeout=5, socket_connect_timeout=5)
    owned_keys = []
    wanted = "get_quote"
    history = [{"time": f"2026-10-19 09:{index:02d}:00", "open": 500, "high": 501, "low": 499, "close": 500, "volume": 10, "source": "virtual"} for index in range(30)]

    class Provider:
        def __init__(self):
            self.calls = 0

        async def chat_stream(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                yield StreamEvent(type="tool_call", tool_name=wanted, tool_args=json.dumps({"symbol": symbol, "period": "5m", "indicator": "MA", "params": {"period": 5}, "trading_mode": "live"}), tool_call_id="scope-fixture")
            else:
                yield StreamEvent(type="message", content="分盘验证结束")

    try:
        await redis_conn.ping()
        for key, payload in ((quote_key("live", symbol), {"symbol": symbol, "last_price": 100}), (quote_key("virtual", symbol), {"symbol": symbol, "last_price": 500, "source": "ctp:openctp_724"}), (history_kline_key("virtual", symbol, "5m"), history)):
            if not await redis_conn.set(key, json.dumps(payload), nx=True, ex=120):
                raise RuntimeError("QA cache key collision")
            owned_keys.append(key)
        application.state.redis = redis_conn
        with patch.object(gateway, "create_provider", side_effect=lambda *args, **kwargs: Provider()), patch("app.connectors.sina.client.fetch_quote_via_bridge", side_effect=AssertionError("No upstream quote permitted")), patch("app.data.kline_provider.fetch_kline_with_fallback", new_callable=AsyncMock, side_effect=AssertionError("No upstream history permitted")):
            for wanted in ("get_quote", "get_kline", "calculate_indicator"):
                response = await client.post("/api/ai/chat", headers=headers(owner_id), json={"model_id": "qa-b02"})
                payload = read_tool_result(response)
                check(f"real_redis_{wanted}_virtual_mode_from_jwt", payload.get("success") is True and payload.get("trading_mode") == "virtual")
                price = payload["quote"]["last_price"] if wanted == "get_quote" else payload["bars"][-1]["close"] if wanted == "get_kline" else payload["latest"]["value"]
                check(f"real_redis_{wanted}_not_live_price", price == 500)
            capability = (await client.get("/api/ai/tools", headers=headers(owner_id))).json()["items"]
            check("restored_read_capabilities_are_enabled", all(next(item for item in capability if item["name"] == name)["enabled"] for name in ("get_quote", "get_kline", "calculate_indicator")))
            await redis_conn.delete(quote_key("virtual", symbol), history_kline_key("virtual", symbol, "5m"))
            for wanted in ("get_quote", "get_kline", "calculate_indicator"):
                payload = read_tool_result(await client.post("/api/ai/chat", headers=headers(owner_id), json={"model_id": "qa-b02"}))
                check(f"real_redis_{wanted}_missing_virtual_not_live_fallback", payload.get("success") is False and payload.get("error_code") == "missing_market_data")
    finally:
        application.state.redis = None
        try:
            if owned_keys:
                if not all(symbol in key and key.startswith(("market:live:quote:", "market:virtual:quote:", "kline:virtual:history:")) for key in owned_keys):
                    raise RuntimeError("Unsafe Redis cleanup target")
                await redis_conn.delete(*owned_keys)
                check("isolated_redis_qa_keys_cleaned", not any([await redis_conn.exists(key) for key in owned_keys]))
        finally:
            await redis_conn.aclose()

    dates = pd.bdate_range("2026-01-05", periods=70)
    codes = [f"{600001 + index:06d}" for index in range(30)]
    close = pd.DataFrame({code: [100 + index + day * (index + 1) / 100 for day in range(70)] for index, code in enumerate(codes)}, index=dates)
    pe = pd.DataFrame([[-20, 0, *range(10, 38)]] * 70, index=dates, columns=codes)
    market = MarketData(close=close, valuation={"pe_ttm": pe}, financial={}, industry={}, name={})
    result = run_portfolio_backtest(market, BacktestParams(factor_ids=["pe_ttm"], start_date=str(dates[0].date()), end_date=str(dates[-1].date()), top_n=3))
    check("real_value_policy_backtest_computes", not result.get("error") and result.get("method_version") == METHOD_VERSION)
    check("negative_and_zero_pe_not_selected", bool(result["trades"]) and all(trade["code"] not in codes[:2] for trade in result["trades"]))
    policy = result["factor_policies"][0]
    check("value_policy_counts_are_saved", policy["eligibility_rule"] == "finite_strictly_positive" and policy["counts"]["excluded_nonpositive"] == 140)
    application.include_router(job_api.router)
    application.dependency_overrides[get_settings] = lambda: settings.model_copy(update={"stock_research_jobs_enabled": True})
    async with scoped() as session:
        session.add(StockResearchSchemaVersion(version=job_api.SCHEMA_VERSION, checksum=hashlib.sha256(job_api.SCHEMA_PATH.read_bytes()).hexdigest()))
        await session.commit()
        body = TypeAdapter(CreateResearchJob).validate_python({"kind": "backtest", "idempotency_key": str(uuid4()), "parameters": {"factor_ids": ["pe_ttm"], "start_date": str(dates[0].date()), "end_date": str(dates[-1].date()), "top_n": 3}})
        saved = await ResearchJobService(session, stock_id, "virtual").create(body, request_id=uuid4().hex)
        await session.commit()
        job_id = UUID(saved["id"])
    async with scoped() as session:
        claimed = await claim_next_job(session)
        worker_token = claimed.worker_token
        check("policy_job_claimed_through_state_machine", claimed.id == job_id)
        await session.commit()
    manifest = {"data_version": "a" * 64, "immutable_archive": False, "point_in_time_certified": False}
    async with scoped() as session:
        check("policy_result_persisted_through_finish_job", await finish_job(session, job_id, worker_token, result=result, manifest=manifest))
        await session.commit()
    response = await client.get(f"/api/stock/research/jobs/{job_id}/result", headers=headers(stock_id))
    check("policy_report_reopens_over_http", response.status_code == 200 and response.json().get("factor_policies") == result["factor_policies"])
    async with scoped() as session:
        await session.execute(update(User).where(User.id == other_id).values(market_type="stock"))
        await session.commit()
    check("policy_report_other_user_404", (await client.get(f"/api/stock/research/jobs/{job_id}/result", headers=headers(other_id))).status_code == 404)
    check("policy_report_other_mode_404", (await client.get(f"/api/stock/research/jobs/{job_id}/result", headers=headers(stock_id, "live"))).status_code == 404)


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b03"))
