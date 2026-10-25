"""验证 _get_period_start_time：分钟线为周期起点时刻（与 VVTR 历史一致），日线按交易日"""

from datetime import datetime, timezone, timedelta

from app.services.kline import _get_period_start_time

_BJ_TZ = timezone(timedelta(hours=8))


def _bj(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_BJ_TZ)


# ========== 日线测试 ==========


def test_daily_daytime_returns_today():
    """白天 10:00 → 日线 period 为当日零点"""
    result = _get_period_start_time("1d", _bj(2026, 6, 4, 10, 0))
    assert result == _bj(2026, 6, 4, 0, 0)


def test_daily_afternoon_returns_today():
    """下午 14:30 → 日线 period 为当日零点"""
    result = _get_period_start_time("1d", _bj(2026, 6, 4, 14, 30))
    assert result == _bj(2026, 6, 4, 0, 0)


def test_daily_night_session_returns_next_day():
    """夜盘 21:00 → 日线 period 为次日零点"""
    result = _get_period_start_time("1d", _bj(2026, 6, 4, 21, 0))
    assert result == _bj(2026, 6, 5, 0, 0)


def test_daily_night_session_2230_returns_next_day():
    """夜盘 22:30 → 日线 period 为次日零点"""
    result = _get_period_start_time("1d", _bj(2026, 6, 4, 22, 30))
    assert result == _bj(2026, 6, 5, 0, 0)


def test_daily_midnight_returns_same_day():
    """凌晨 01:00 → 日线 period 为当日零点"""
    result = _get_period_start_time("1d", _bj(2026, 6, 5, 1, 0))
    assert result == _bj(2026, 6, 5, 0, 0)


# ========== 分钟线：起点时刻标签（与 VVTR 历史 b=bob 语义一致） ==========


def test_1m_period_start_label():
    """1m：10:35:xx → 时间键 10:35（本分钟起点）"""
    result = _get_period_start_time("1m", _bj(2026, 6, 4, 10, 35))
    assert result == _bj(2026, 6, 4, 10, 35)


def test_1m_open_first_minute():
    """1m：09:00 → 09:00（首分钟起点，不再超前）"""
    result = _get_period_start_time("1m", _bj(2026, 6, 4, 9, 0))
    assert result == _bj(2026, 6, 4, 9, 0)


def test_5m_period_start_label():
    """5m：10:37 → 10:35"""
    result = _get_period_start_time("5m", _bj(2026, 6, 4, 10, 37))
    assert result == _bj(2026, 6, 4, 10, 35)


def test_15m_period_start_label():
    """15m：10:37 → 10:30"""
    result = _get_period_start_time("15m", _bj(2026, 6, 4, 10, 37))
    assert result == _bj(2026, 6, 4, 10, 30)


def test_15m_on_boundary_keeps_current():
    """15m：恰好 10:45 → 仍属 10:45-11:00 桶，起点 10:45"""
    result = _get_period_start_time("15m", _bj(2026, 6, 4, 10, 45))
    assert result == _bj(2026, 6, 4, 10, 45)


def test_30m_period_start_label():
    """30m：10:45 → 10:30"""
    result = _get_period_start_time("30m", _bj(2026, 6, 4, 10, 45))
    assert result == _bj(2026, 6, 4, 10, 30)


def test_60m_period_start_label():
    """60m：10:45 → 10:00"""
    result = _get_period_start_time("60m", _bj(2026, 6, 4, 10, 45))
    assert result == _bj(2026, 6, 4, 10, 0)


# ========== 实盘：按品种交易分钟轴对齐（起点语义） ==========


def test_rb_5m_and_15m_resume_from_trading_minute_axis():
    """商品 10:30 复市后，5m/15m 当前桶起点分别为 10:30/10:30。"""
    now = _bj(2026, 7, 29, 10, 31)
    assert _get_period_start_time("5m", now, "rb2610") == _bj(
        2026, 7, 29, 10, 30
    )
    assert _get_period_start_time("15m", now, "rb2610") == _bj(
        2026, 7, 29, 10, 30
    )


def test_rb_exact_break_closing_print_folds_into_closing_bucket():
    """10:15 闭市整点的收盘打印按左闭右开归入收盘桶（vnpy 社区/主流行情
    软件口径：边界整点 tick 属上一根），不再跳到复市 10:30 开幽灵桶。"""
    now = _bj(2026, 7, 29, 10, 15)
    assert _get_period_start_time("1m", now, "rb2610") == _bj(
        2026, 7, 29, 10, 14
    )
    assert _get_period_start_time("5m", now, "rb2610") == _bj(
        2026, 7, 29, 10, 10
    )
    assert _get_period_start_time("15m", now, "rb2610") == _bj(
        2026, 7, 29, 10, 0
    )
    # 30m 在 10:15 尚未累计满 30 个交易分钟，当前桶为 10:00-10:45。
    assert _get_period_start_time("30m", now, "rb2610") == _bj(
        2026, 7, 29, 10, 0
    )
    # 迟到的收盘打印（收盘后 30 秒内到达）同样归收盘桶
    late = _bj(2026, 7, 29, 10, 15).replace(second=30)
    assert _get_period_start_time("1m", late, "rb2610") == _bj(
        2026, 7, 29, 10, 14
    )
    assert _get_period_start_time("15m", late, "rb2610") == _bj(
        2026, 7, 29, 10, 0
    )


def test_rb_30m_skips_breaks_without_resetting_bucket():
    """今日 rb2610 原生 30m：10:31 属于 10:00-10:45 桶，11:16 属于 11:15-13:45 桶。"""
    assert _get_period_start_time(
        "30m", _bj(2026, 7, 29, 10, 31), "rb2610"
    ) == _bj(2026, 7, 29, 10, 0)
    assert _get_period_start_time(
        "30m", _bj(2026, 7, 29, 11, 16), "rb2610"
    ) == _bj(2026, 7, 29, 11, 15)


def test_rb_30m_break_time_keeps_bucket_start():
    """小休期间当前 30m 桶仍为 10:00-10:45，不退回自然 10:30 桶。"""
    assert _get_period_start_time(
        "30m", _bj(2026, 7, 29, 10, 20), "rb2610"
    ) == _bj(2026, 7, 29, 10, 0)


def test_rb_60m_skips_breaks_without_resetting_bucket():
    """今日 rb2610 原生 60m：10:31 属于 10:00-11:15 桶，11:16 属于 11:15-14:15 桶。"""
    assert _get_period_start_time(
        "60m", _bj(2026, 7, 29, 10, 31), "rb2610"
    ) == _bj(2026, 7, 29, 10, 0)
    assert _get_period_start_time(
        "60m", _bj(2026, 7, 29, 11, 16), "rb2610"
    ) == _bj(2026, 7, 29, 11, 15)


def test_rb_day_close_partial_period_starts_on_axis():
    """商品日盘尾段不足完整周期：30m 末桶 14:45-15:00，60m 末桶 14:15-15:00。"""
    now = _bj(2026, 7, 29, 14, 46)
    assert _get_period_start_time("30m", now, "rb2610") == _bj(
        2026, 7, 29, 14, 45
    )
    assert _get_period_start_time("60m", now, "rb2610") == _bj(
        2026, 7, 29, 14, 15
    )


def test_rb_night_session_uses_2100_as_anchor():
    """夜盘独立从 21:00 起算：21:01 的 30m/60m 桶起点均为 21:00。"""
    now = _bj(2026, 7, 28, 21, 1)
    assert _get_period_start_time("30m", now, "rb2610") == _bj(
        2026, 7, 28, 21, 0
    )
    assert _get_period_start_time("60m", now, "rb2610") == _bj(
        2026, 7, 28, 21, 0
    )


def test_au_60m_carries_unfinished_night_bucket_into_day_session():
    """贵金属夜盘 330 交易分钟，60m 网格延续到日盘：夜尾桶 02:00-09:30。"""
    assert _get_period_start_time(
        "60m", _bj(2026, 7, 29, 2, 15), "au2608"
    ) == _bj(2026, 7, 29, 2, 0)
    assert _get_period_start_time(
        "60m", _bj(2026, 7, 29, 9, 1), "au2608"
    ) == _bj(2026, 7, 29, 2, 0)
    # 09:31 属于日盘网格桶 09:30-10:45
    assert _get_period_start_time(
        "60m", _bj(2026, 7, 29, 9, 31), "au2608"
    ) == _bj(2026, 7, 29, 9, 30)


def test_au_friday_night_remainder_keeps_saturday_axis_start():
    """周五夜盘 23:45 属 23:00-00:00 桶；周六凌晨 03:00 属 02:00-09:30 桶。"""
    assert _get_period_start_time(
        "60m", _bj(2026, 7, 31, 23, 45), "au2608"
    ) == _bj(2026, 7, 31, 23, 0)
    assert _get_period_start_time(
        "60m", _bj(2026, 8, 1, 3, 0), "au2608"
    ) == _bj(2026, 8, 1, 2, 0)


def test_au_monday_early_morning_maps_to_friday_night_bucket():
    """不存在周日夜盘：周一凌晨的轴桶 = 周五夜盘余段（周六 02:00 起）。

    修复前：周日晚会被拼成幻影交易轴，周一凌晨的 60m 桶起点错误地
    落在「周一 02:00」；修复后回退到周五夜盘（周六 02:00-周一 09:30 桶）。
    """
    assert _get_period_start_time(
        "60m", _bj(2026, 8, 3, 3, 0), "au2608"
    ) == _bj(2026, 8, 1, 2, 0)


def test_if_60m_uses_0930_session_open_as_anchor():
    """股指 60m 首桶 09:30-10:30，起点为 09:30。"""
    assert _get_period_start_time(
        "60m", _bj(2026, 7, 29, 9, 31), "if2608"
    ) == _bj(2026, 7, 29, 9, 30)


def test_bond_final_partial_period_starts_on_axis():
    """国债期货尾段不足 30/60 分钟：末桶起点均为 15:00（收线 15:15）。"""
    now = _bj(2026, 7, 29, 15, 1)
    assert _get_period_start_time("30m", now, "t2609") == _bj(
        2026, 7, 29, 15, 0
    )
    assert _get_period_start_time("60m", now, "t2609") == _bj(
        2026, 7, 29, 15, 0
    )


# ========== 收盘打印边界（左闭右开）：2026-08-25 夜盘→日盘开盘错乱回归 ==========


def test_night_close_print_folds_into_last_night_bucket():
    """23:00 整的夜盘收盘打印归入夜盘末桶，不再生成「次日 09:00」幽灵桶。

    修复前：period_start(23:00:00) 经 prefer_next_at_break 跳到次日 09:00，
    幽灵 forming bar 跨夜存活，开盘首 tick 同键合并 → 新 K 开盘价被
    粘成昨夜收盘打印价，且真实末根（22:45/22:59）收线固化丢失。
    """
    close = _bj(2026, 8, 24, 23, 0)
    assert _get_period_start_time("15m", close, "pp2701") == _bj(
        2026, 8, 24, 22, 45
    )
    assert _get_period_start_time("30m", close, "pp2701") == _bj(
        2026, 8, 24, 22, 30
    )
    assert _get_period_start_time("5m", close, "pp2701") == _bj(
        2026, 8, 24, 22, 55
    )
    assert _get_period_start_time("1m", close, "pp2701") == _bj(
        2026, 8, 24, 22, 59
    )
    # 迟到 30 秒的收盘打印同样归收盘桶
    late = _bj(2026, 8, 24, 23, 0).replace(second=30)
    assert _get_period_start_time("15m", late, "pp2701") == _bj(
        2026, 8, 24, 22, 45
    )
    assert _get_period_start_time("1m", late, "pp2701") == _bj(
        2026, 8, 24, 22, 59
    )


def test_morning_open_and_precall_map_to_opening_bucket():
    """次日 09:00 整开盘 tick、08:59 集合竞价 tick → 09:00 开盘新桶。"""
    assert _get_period_start_time("15m", _bj(2026, 8, 25, 9, 0), "pp2701") == _bj(
        2026, 8, 25, 9, 0
    )
    assert _get_period_start_time("1m", _bj(2026, 8, 25, 8, 59), "pp2701") == _bj(
        2026, 8, 25, 9, 0
    )


def test_day_close_print_folds_into_last_day_bucket():
    """15:00 整的日盘收盘打印归入末桶，不再产生「15:00」幽灵 bar。"""
    close = _bj(2026, 7, 29, 15, 0)
    assert _get_period_start_time("15m", close, "rb2610") == _bj(
        2026, 7, 29, 14, 45
    )
    assert _get_period_start_time("1m", close, "rb2610") == _bj(
        2026, 7, 29, 14, 59
    )
    assert _get_period_start_time("30m", close, "rb2610") == _bj(
        2026, 7, 29, 14, 45
    )


def test_lunch_close_print_folds_into_morning_last_bucket():
    """11:30 整午休收盘打印归入上午末桶。"""
    close = _bj(2026, 7, 29, 11, 30)
    assert _get_period_start_time("15m", close, "rb2610") == _bj(
        2026, 7, 29, 11, 15
    )
    assert _get_period_start_time("1m", close, "rb2610") == _bj(
        2026, 7, 29, 11, 29
    )


def test_au_night_close_print_folds_into_spanning_bucket():
    """02:30 整收盘打印归入跨夜到日盘的 60m 末桶（02:00 起）。"""
    close = _bj(2026, 7, 29, 2, 30)
    assert _get_period_start_time("60m", close, "au2608") == _bj(
        2026, 7, 29, 2, 0
    )
    assert _get_period_start_time("30m", close, "au2608") == _bj(
        2026, 7, 29, 2, 0
    )
    assert _get_period_start_time("1m", close, "au2608") == _bj(
        2026, 7, 29, 2, 29
    )


# ========== 实时 tick 整分边界（00 秒收盘打印归上一桶）：2026-09-10 口径统一 ==========

from app.services.session_profiles import fold_tick_minute_edge  # noqa: E402


def test_minute_edge_folds_plain_whole_minute_tick():
    """1m 边界：10:01:00 整分 tick 是 10:00 桶的收盘打印 → 折回上一分钟内侧。

    与深历史 pg-tickdata 同轴：VVTR 的 1m 行 eob=X 覆盖 [X-1, X)，时间戳
    00 秒整的成交位于下一行、读取时折叠进上一桶；实时 forming 修复前把
    00 秒整 tick 开进新桶，导致上一桶 close/volume 少最后一笔、与 VVTR
    回补值交接时整根跳变。
    """
    at = _bj(2026, 7, 29, 10, 1)
    folded = fold_tick_minute_edge("rb", at)
    assert folded == at - timedelta(microseconds=1)
    assert _get_period_start_time("1m", folded, "rb2610") == _bj(
        2026, 7, 29, 10, 0
    )


def test_multi_period_whole_minute_edges_fold_to_previous_bucket():
    """多分钟整分边界同口径：10:05:00 → 10:00 桶；10:45:00 → 10:30 桶。"""
    folded = fold_tick_minute_edge("rb", _bj(2026, 7, 29, 10, 5))
    assert _get_period_start_time("5m", folded, "rb2610") == _bj(
        2026, 7, 29, 10, 0
    )
    folded = fold_tick_minute_edge("rb", _bj(2026, 7, 29, 10, 45))
    assert _get_period_start_time("15m", folded, "rb2610") == _bj(
        2026, 7, 29, 10, 30
    )
    folded = fold_tick_minute_edge("rb", _bj(2026, 7, 29, 10, 30))
    assert _get_period_start_time("30m", folded, "rb2610") == _bj(
        2026, 7, 29, 10, 0
    )


def test_session_open_whole_minute_tick_not_folded():
    """开盘整点（09:00 日盘 / 21:00 夜盘 / 10:30 复市）归开盘新桶，不折。"""
    assert fold_tick_minute_edge("rb", _bj(2026, 7, 29, 9, 0)) == _bj(
        2026, 7, 29, 9, 0
    )
    assert fold_tick_minute_edge("rb", _bj(2026, 7, 28, 21, 0)) == _bj(
        2026, 7, 28, 21, 0
    )
    assert fold_tick_minute_edge("rb", _bj(2026, 7, 29, 10, 30)) == _bj(
        2026, 7, 29, 10, 30
    )


def test_session_close_whole_minute_tick_folds():
    """收盘整点（10:15 小休 / 15:00 日收盘 / 23:00 夜收盘）折入收盘桶。"""
    assert fold_tick_minute_edge("rb", _bj(2026, 7, 29, 10, 15)) < _bj(
        2026, 7, 29, 10, 15
    )
    assert fold_tick_minute_edge("rb", _bj(2026, 7, 29, 15, 0)) < _bj(
        2026, 7, 29, 15, 0
    )
    assert fold_tick_minute_edge("pp", _bj(2026, 8, 24, 23, 0)) < _bj(
        2026, 8, 24, 23, 0
    )


def test_non_whole_minute_and_off_axis_not_folded():
    """非整分原样；轴外整分（周末）原样。"""
    at = _bj(2026, 7, 29, 10, 1).replace(second=30)
    assert fold_tick_minute_edge("rb", at) is at
    saturday = _bj(2026, 8, 1, 10, 0)
    assert fold_tick_minute_edge("rb", saturday) is saturday
