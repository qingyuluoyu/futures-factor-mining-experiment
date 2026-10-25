"""连续合约分钟翻页批次边界回归（2026-09-01 rb9999 排查）

根因：分发源链（vvtr/新浪）的 bar time 为分钟精度（无秒），客户端翻页
跨源边界时把无秒游标带进 tickdata 深历史路径；_parse_end_time 原实现只
认 "%Y-%m-%d %H:%M:%S"，解析成 None 后历史页退化成"最新视图"语义，被
新鲜度守卫整页拦截 → bars=[] 且 has_more=false，翻页在批次边界终止。

实测对照（生产库）：同一页游标 "2026-04-16 13:30" 返回 0 根，
"2026-04-16 13:30:00" 返回 500 根——数据层分期主力拼接本身工作正常。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.data import tickdata_kline as td


# ============ 游标格式解析矩阵 ============


def test_parse_end_time_formats():
    """三种合法格式 + 非法/空串 → None（口径对齐 _parse_bar_clock）"""
    assert td._parse_end_time("2026-04-16 13:30:00") == datetime(2026, 4, 16, 13, 30)
    # 回归主体：分钟精度（分发源跨源游标）此前解析成 None
    assert td._parse_end_time("2026-04-16 13:30") == datetime(2026, 4, 16, 13, 30)
    assert td._parse_end_time("2026-04-16") == datetime(2026, 4, 16)
    # 多余精度截断（毫秒等）
    assert td._parse_end_time("2026-04-16 13:30:00.123") == datetime(2026, 4, 16, 13, 30)
    assert td._parse_end_time("") is None
    assert td._parse_end_time("garbage") is None


# ============ 批次边界翻页：分期主力拼接无缝接管 ============


def _minute_rows(day: datetime, n_bars: int = 12) -> list[dict]:
    """单日日盘 15m 网格（eob 口径，09:30 起第 1 根收盘）"""
    first_eob = day.replace(hour=9, minute=30, second=0, microsecond=0)
    return [
        {
            "ts": first_eob + timedelta(minutes=15 * i),
            "open": 3000.0, "high": 3010.0, "low": 2990.0,
            "close": 3005.0, "volume": 100, "position": 5000,
        }
        for i in range(n_bars)
    ]


def _make_pool(dom_days: list[datetime], own_days: list[datetime] | None = None):
    """fake pool：own(rb9999)=own_days（缺省空=批次边界之上无自有数据）、
    日历=rb2510、rb2510 分钟行存在——模拟批次边界页的完整查询形态。"""

    class _Conn:
        async def fetch(self, sql, *args, **kwargs):
            if "period = 1440" in sql:
                # 分期主力日历（窗口函数查询）
                return [
                    {"ts": d.replace(hour=15), "instrument": "rb2510"}
                    for d in dom_days
                ]
            # 单合约分钟行查询：参数 (pg_period, instrument, start, end, n)
            instrument = args[1] if len(args) >= 2 else ""
            if instrument == "rb9999":
                if own_days:
                    rows: list[dict] = []
                    for d in own_days:
                        rows.extend(_minute_rows(d))
                    return rows
                return []  # 连续合约批次段从更晚才开始 → 本窗口为空
            if instrument == "rb2510":
                rows = []
                for d in dom_days:
                    rows.extend(_minute_rows(d))
                return rows
            return []

    class _Acq:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Acq()

        async def close(self):
            return None

    return _Pool()


def _run(monkeypatch, end_time: str, dom_days: list[datetime], own_days=None):
    import asyncio

    pool = _make_pool(dom_days, own_days)

    async def _fake_pool():
        return pool

    monkeypatch.setattr(td, "_get_pool", _fake_pool)
    return asyncio.run(
        td.fetch_tickdata_kline("rb9999", "15m", end_time=end_time, limit=200)
    )


def test_batch_boundary_minute_cursor_stitches(monkeypatch):
    """无秒游标（分发源跨源形态）在批次边界必须由分期主力拼接接管

    修复前：_parse_end_time("...13:30") → None → 最新视图语义 →
    own 查询无上界、新鲜度守卫拦截 → 返回 []，翻页终止。
    """
    # 主力日历：2025-06-09 ~ 06-13（周一~周五），全部 rb2510
    dom_days = [datetime(2025, 6, 9) + timedelta(days=i) for i in range(5)]
    cursor = "2025-06-15 13:30"  # 无秒游标，距日历末根 2 天

    out = _run(monkeypatch, cursor, dom_days)
    assert out, "批次边界页必须返回拼接段（修复前为空）"
    times = [b["time"] for b in out]
    assert times == sorted(times)
    # 拼接段全部早于游标（翻页方向正确，无越界泄漏）
    assert all(t < "2025-06-15 13:30" for t in times)
    # 蜡烛字段完整（可直接并入客户端序列）
    assert all(
        set(b) >= {"time", "open", "high", "low", "close", "volume"} for b in out
    )


def test_batch_boundary_second_cursor_parity(monkeypatch):
    """带秒游标与无秒游标同一窗口结果一致（跨源游标连续性）"""
    dom_days = [datetime(2025, 6, 9) + timedelta(days=i) for i in range(5)]
    no_sec = _run(monkeypatch, "2025-06-15 13:30", dom_days)
    with_sec = _run(monkeypatch, "2025-06-15 13:30:00", dom_days)
    assert [b["time"] for b in no_sec] == [b["time"] for b in with_sec]


def test_window_hole_fills_page(monkeypatch):
    """own 段止于窗口中段（批次边界洞）：短页必须由拼接补满整页

    修复前：uncovered 只查窗口头部（first_ts ≈ start_dt 即判已覆盖），
    中段洞不触发拼接，且拼接窗口 end_bound=first_ts 为空区间——短页
    返回后 API 判 has_more=false，翻页在洞处提前终止（rb9999 游标
    07-02 实测：202 根短页 + 翻页终止）。
    """
    # 窗口 ≈ [2025-04-27, 06-15)：own 段只有 05-01~05-07（头部），其后到
    # 游标之间是洞；分期主力 rb2510 全窗口有数据
    own_days = [datetime(2025, 5, d) for d in range(1, 8)]
    dom_days = [datetime(2025, 4, 28) + timedelta(days=i) for i in range(47)]
    out = _run(monkeypatch, "2025-06-15 13:30", dom_days, own_days=own_days)
    # 洞被拼接填满 → 整页 200 根（修复前 own 段 ~84 根短页）
    assert len(out) == 200, f"洞页必须补满整页，实际 {len(out)}"
    times = [b["time"] for b in out]
    assert times == sorted(times)
    assert all(t < "2025-06-15 13:30" for t in times)
