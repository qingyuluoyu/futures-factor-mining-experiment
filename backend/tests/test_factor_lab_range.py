"""factor_lab load_bars 长历史区间模式单元测试

不传日期 → 近期路径（TF_LIMIT、end_time=None）保持不变；
传日期 → 区间模式走 get_kline_history(end_time=...)（tickdata 长历史），
校验复用策略回测的 validate_range，并裁剪到 [start, end]。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.factor_lab.common import TF_LIMIT, load_bars


def _bars_between(start: str, end: str) -> list[dict]:
    """生成 start~end 每日一根的合成日线（含边界）"""
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    out: list[dict] = []
    d = d0
    while d <= d1:
        out.append(
            {
                "time": d.isoformat(),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000,
            }
        )
        d += timedelta(days=1)
    return out


async def test_load_bars_recent_mode_unchanged() -> None:
    """不传日期：仍走近期路径（end_time=None + TF_LIMIT）"""
    m = AsyncMock(return_value=_bars_between("2024-01-01", "2024-01-10"))
    with patch("app.api.factor_lab.common.get_kline_history", new=m):
        bars = await load_bars(None, None, "rb2610", "1d")
    assert len(bars) == 10
    m.assert_awaited_once()
    _, kwargs = m.await_args
    assert kwargs["end_time"] is None
    assert kwargs["limit"] == TF_LIMIT["1d"]


async def test_load_bars_ranged_mode_passes_end_time_and_limit() -> None:
    """传日期：end_time=末日 23:59:59，日线 limit 按区间天数推导"""
    m = AsyncMock(return_value=_bars_between("2023-01-01", "2024-06-30"))
    with patch("app.api.factor_lab.common.get_kline_history", new=m):
        bars = await load_bars(
            None, None, "rb2610", "1d", "2023-01-01", "2024-06-30"
        )
    assert bars
    _, kwargs = m.await_args
    assert kwargs["end_time"] == "2024-06-30 23:59:59"
    days = 546  # 2023-01-01 ~ 2024-06-30
    assert kwargs["limit"] == int(days * 1.5) + 200


async def test_load_bars_ranged_mode_trims_out_of_range() -> None:
    """数据源多给（早于 start / 晚于 end）的 bar 被裁掉"""
    extra = (
        _bars_between("2023-12-20", "2023-12-31")  # 早于 start
        + _bars_between("2024-01-01", "2024-03-31")  # 区间内
        + _bars_between("2024-04-01", "2024-04-15")  # 晚于 end
    )
    m = AsyncMock(return_value=extra)
    with patch("app.api.factor_lab.common.get_kline_history", new=m):
        bars = await load_bars(
            None, None, "rb2610", "1d", "2024-01-01", "2024-03-31"
        )
    assert bars[0]["time"] == "2024-01-01"
    assert bars[-1]["time"] == "2024-03-31"


async def test_load_bars_ranged_mode_minute_limit() -> None:
    """分钟线 limit 按交易日×每日根数推导"""
    m = AsyncMock(return_value=_bars_between("2024-01-01", "2024-01-31"))
    with patch("app.api.factor_lab.common.get_kline_history", new=m):
        await load_bars(None, None, "rb2610", "60m", "2024-01-01", "2024-01-31")
    _, kwargs = m.await_args
    trading_days = 30 * 5 // 7
    assert kwargs["limit"] == trading_days * 4 + 1000




async def test_load_bars_15m_range_within_factor_cap() -> None:
    """15m 因子区间 90 天（旧策略口径 30 天会拒，新因子口径放行）"""
    m = AsyncMock(return_value=_bars_between("2024-01-01", "2024-03-31"))
    with patch("app.api.factor_lab.common.get_kline_history", new=m):
        bars = await load_bars(
            None, None, "rb2610", "15m", "2024-01-01", "2024-03-31"
        )
    assert bars
    _, kwargs = m.await_args
    assert kwargs["end_time"] == "2024-03-31 23:59:59"


async def test_load_bars_15m_range_beyond_factor_cap_rejected() -> None:
    """15m 因子区间超过 180 天 → 400（因子口径上限）"""
    m = AsyncMock(return_value=[])
    with patch("app.api.factor_lab.common.get_kline_history", new=m):
        with pytest.raises(HTTPException) as ei:
            await load_bars(
                None, None, "rb2610", "15m", "2023-06-01", "2024-06-01"
            )
    assert ei.value.status_code == 400
    assert "最多 180" in ei.value.detail
    m.assert_not_awaited()

@pytest.mark.parametrize(
    "start,end,detail_part",
    [
        ("2024-06-30", "2024-01-01", "不能早于"),  # start > end
        ("2015-01-01", "2024-01-01", "最多"),  # 超过 1d 上限 1825 天
    ],
)
async def test_load_bars_ranged_mode_rejects_bad_range(
    start: str, end: str, detail_part: str
) -> None:
    """非法区间 → 400（文案与策略回测一致）"""
    m = AsyncMock(return_value=[])
    with patch("app.api.factor_lab.common.get_kline_history", new=m):
        with pytest.raises(HTTPException) as ei:
            await load_bars(None, None, "rb2610", "1d", start, end)
    assert ei.value.status_code == 400
    assert detail_part in ei.value.detail
    m.assert_not_awaited()


async def test_load_bars_rejects_half_date_pair() -> None:
    """只传一个日期 → 400，且不打数据源"""
    m = AsyncMock(return_value=[])
    with patch("app.api.factor_lab.common.get_kline_history", new=m):
        with pytest.raises(HTTPException) as ei:
            await load_bars(None, None, "rb2610", "1d", "2024-01-01", None)
    assert ei.value.status_code == 400
    m.assert_not_awaited()


async def test_load_bars_ranged_mode_empty_bars_hint() -> None:
    """区间内无数据 → 400，提示长历史库可能未配置"""
    m = AsyncMock(return_value=_bars_between("2020-01-01", "2020-02-01"))
    with patch("app.api.factor_lab.common.get_kline_history", new=m):
        with pytest.raises(HTTPException) as ei:
            await load_bars(
                None, None, "rb2610", "1d", "2024-01-01", "2024-03-31"
            )
    assert ei.value.status_code == 400
    assert "长历史库" in ei.value.detail
