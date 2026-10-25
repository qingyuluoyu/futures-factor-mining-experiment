import json

import pytest

from app.api import ws


@pytest.mark.asyncio
async def test_cached_values_are_read_with_one_mget() -> None:
    class FakeRedis:
        async def scan_iter(self, _pattern: str):
            yield "market:live:quote:rb2610"
            yield "market:live:quote:au2612"

        async def mget(self, keys: list[str]) -> list[str]:
            assert keys == [
                "market:live:quote:rb2610",
                "market:live:quote:au2612",
            ]
            return [json.dumps({"symbol": "rb2610"}), json.dumps({"symbol": "au2612"})]

        async def get(self, _key: str) -> str:
            raise AssertionError("cache reads must not issue sequential GET calls")

    result = await ws._read_cached_by_pattern(FakeRedis(), "market:live:quote:*")

    assert result == [{"symbol": "rb2610"}, {"symbol": "au2612"}]


@pytest.mark.asyncio
async def test_cached_trades_are_read_with_one_pipeline() -> None:
    class FakePipeline:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def lrange(self, key: str, _start: int, _end: int) -> "FakePipeline":
            self.keys.append(key)
            return self

        async def execute(self) -> list[list[str]]:
            assert self.keys == [
                "market:live:trades:rb2610",
                "market:live:trades:au2612",
            ]
            return [
                [json.dumps({"price": 3075})],
                [json.dumps({"price": 800})],
            ]

    class FakeRedis:
        async def scan_iter(self, _pattern: str):
            yield "market:live:trades:rb2610"
            yield "market:live:trades:au2612"

        def pipeline(self, *, transaction: bool) -> FakePipeline:
            assert transaction is False
            return FakePipeline()

        async def lrange(self, _key: str, _start: int, _end: int) -> list[str]:
            raise AssertionError("trade reads must not issue sequential LRANGE calls")

    result = await ws._read_cached_trades(FakeRedis(), "live")

    assert result == {
        "rb2610": [{"price": 3075}],
        "au2612": [{"price": 800}],
    }


def test_poll_deadline_does_not_accumulate_broadcast_cost() -> None:
    assert ws._advance_poll_deadline(10.0, 10.02, 0.05) == pytest.approx(10.05)


def test_poll_deadline_restarts_after_an_overrun() -> None:
    assert ws._advance_poll_deadline(10.0, 10.08, 0.05) == pytest.approx(10.13)
