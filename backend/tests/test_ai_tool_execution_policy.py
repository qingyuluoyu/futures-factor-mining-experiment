import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services.ai.tools.loop import execute_tool_with_retry, execute_tools_parallel


@pytest.mark.asyncio
@pytest.mark.parametrize("name,count", [("get_quote", 2), ("paper_open_long", 1), ("unknown_extension", 1), ("update_indicator_settings", 1)])
async def test_only_allowlisted_reads_retry_after_timeout(name, count):
    with patch("app.services.ai.tools.loop.execute_tool", new_callable=AsyncMock, side_effect=TimeoutError) as execute:
        result = json.loads(await execute_tool_with_retry(name, {}, None, None, None))
    assert result["success"] is False
    assert execute.await_count == count


@pytest.mark.asyncio
async def test_shared_session_is_never_used_concurrently():
    active = 0
    peak = 0

    async def execute(*arguments, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return "{}"

    calls = [{"tool_name": "get_quote", "tool_args": "{}", "tool_call_id": str(index)} for index in range(3)]
    with patch("app.services.ai.tools.loop.execute_tool", side_effect=execute):
        results = await execute_tools_parallel(calls, object(), None, None)
    assert len(results) == 3
    assert peak == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", ["[]", "null", "7", '"text"', "{invalid"])
async def test_malformed_or_non_object_arguments_are_not_executed(arguments):
    call = {"tool_name": "get_quote", "tool_args": arguments, "tool_call_id": "invalid"}
    with patch("app.services.ai.tools.loop.execute_tool", new_callable=AsyncMock) as execute:
        results = await execute_tools_parallel([call], None, None, None)
    execute.assert_not_awaited()
    assert json.loads(results[0][1])["error_code"] == "invalid_tool_arguments"


@pytest.mark.asyncio
async def test_sessionless_reads_still_run_concurrently():
    active = 0
    peak = 0

    async def execute(*arguments, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return "{}"

    calls = [{"tool_name": "get_quote", "tool_args": "{}", "tool_call_id": str(index)} for index in range(3)]
    with patch("app.services.ai.tools.loop.execute_tool", side_effect=execute):
        results = await execute_tools_parallel(calls, None, None, None)
    assert len(results) == 3
    assert peak == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("name,session", [("paper_get_account", None), ("get_quote", object())])
async def test_account_creation_or_shared_transaction_is_not_retried(name, session):
    with patch("app.services.ai.tools.loop.execute_tool", new_callable=AsyncMock, side_effect=TimeoutError) as execute:
        assert json.loads(await execute_tool_with_retry(name, {}, session, None, None))["success"] is False
    assert execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("do not expose secret text"), HTTPException(403, "forbidden")])
async def test_non_timeout_errors_are_not_retried_or_exposed(error):
    with patch("app.services.ai.tools.loop.execute_tool", new_callable=AsyncMock, side_effect=error) as execute:
        result = json.loads(await execute_tool_with_retry("get_quote", {}, None, None, None))
    assert execute.await_count == 1
    assert result["success"] is False
    assert "secret" not in result["error"]
