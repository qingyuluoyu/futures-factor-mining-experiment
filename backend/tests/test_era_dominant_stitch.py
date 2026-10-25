"""分期主力分钟拼接测试 —— 段划分与缺口补齐"""

from __future__ import annotations

from datetime import datetime

from app.data.tickdata_kline import _segment_consecutive


def _row(d: str, ins: str) -> dict:
    return {"ts": datetime.fromisoformat(d), "instrument": ins}


def test_segment_consecutive_groups_runs():
    rows = [
        _row("2024-10-08", "ma2501"), _row("2024-10-09", "ma2501"),
        _row("2024-12-12", "ma2505"), _row("2024-12-13", "ma2505"),
        _row("2024-12-16", "ma2505"),
        _row("2024-12-17", "ma2509"),
    ]
    segs = _segment_consecutive(rows)
    assert segs == [
        ["ma2501", datetime(2024, 10, 8), datetime(2024, 10, 9)],
        ["ma2505", datetime(2024, 12, 12), datetime(2024, 12, 16)],
        ["ma2509", datetime(2024, 12, 17), datetime(2024, 12, 17)],
    ]


def test_segment_empty():
    assert _segment_consecutive([]) == []


def test_segment_alternating_keeps_all():
    rows = [_row("2024-01-01", "a2501"), _row("2024-01-02", "a2505"),
            _row("2024-01-03", "a2501")]
    segs = _segment_consecutive(rows)
    assert len(segs) == 3
