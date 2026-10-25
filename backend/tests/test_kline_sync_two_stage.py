"""两段式 K 线修正契约测试 —— 定时错峰 + 数据源隔离

设计（防 vvtr 压力过大）：
- 第一段 01:00 + 15:05：vvtr_tickdata_sync 从 vvtr 拉 → 写 pg-tickdata
- 第二段 02:00 + 15:30：kline_sync 读 pg-tickdata 修正主 PG，不直连 vvtr
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services import kline_sync, vvtr_tickdata_sync

_BJ = timezone(timedelta(hours=8))


def test_second_stage_fire_times():
    """修正（第二段）必须在 02:00 + 15:30，晚于第一段定点 01:00/15:05"""
    assert kline_sync.SYNC_FIRE_TIMES == [
        (2, 0, "scheduled_0200"),
        (15, 30, "scheduled_1530"),
    ]
    stage1 = sorted(vvtr_tickdata_sync._DAILY_POINTS)
    stage2 = sorted((hh, mm) for hh, mm, _ in kline_sync.SYNC_FIRE_TIMES)
    for (h1, m1), (h2, m2) in zip(stage1, stage2):
        # 同日序上第二段必须晚于第一段（小时更大，或同小时分钟更大）
        assert (h2, m2) > (h1, m1)


def test_first_stage_daily_points():
    """第一段定点 = 01:00 + 15:05"""
    assert sorted(vvtr_tickdata_sync._DAILY_POINTS) == [(1, 0), (15, 5)]


def test_due_daily_point_fires_once_per_day():
    fired: dict[str, str] = {}
    bj = datetime(2026, 8, 24, 1, 5, tzinfo=_BJ)
    assert vvtr_tickdata_sync._due_daily_point(bj, fired) == "01:00"
    assert fired == {"0100": "2026-08-24"}
    # 同天重复检查不再触发
    assert vvtr_tickdata_sync._due_daily_point(bj, fired) is None
    # 15:05 到点触发第二个定点
    bj2 = datetime(2026, 8, 24, 15, 6, tzinfo=_BJ)
    assert vvtr_tickdata_sync._due_daily_point(bj2, fired) == "15:05"
    assert vvtr_tickdata_sync._due_daily_point(bj2, fired) is None


def test_due_daily_point_before_any_point():
    """00:30 未到任何定点：不触发"""
    fired: dict[str, str] = {}
    bj = datetime(2026, 8, 24, 0, 30, tzinfo=_BJ)
    assert vvtr_tickdata_sync._due_daily_point(bj, fired) is None


def test_seconds_to_next_point_unfired_today():
    """00:30（都未跑）→ 最近定点是今天 01:00"""
    fired: dict[str, str] = {}
    bj = datetime(2026, 8, 24, 0, 30, tzinfo=_BJ)
    assert vvtr_tickdata_sync._seconds_to_next_point(bj, fired) == 1800.0


def test_seconds_to_next_point_all_fired_today():
    """今天两个定点都已跑 → 看明天 01:00"""
    fired = {"0100": "2026-08-24", "1505": "2026-08-24"}
    bj = datetime(2026, 8, 24, 16, 0, tzinfo=_BJ)
    # 明天 01:00 距今天 16:00 = 9 小时
    assert vvtr_tickdata_sync._seconds_to_next_point(bj, fired) == 9 * 3600.0


def test_correction_reads_tickdata_not_vvtr():
    """_sync_one_period 的数据源必须是 pg-tickdata 读取器（不 import vvtr）"""
    import inspect

    src = inspect.getsource(kline_sync._sync_one_period)
    assert "fetch_tickdata_kline" in src
    assert "fetch_vvtr_kline" not in src
    assert "vvtr.http_client" not in src
