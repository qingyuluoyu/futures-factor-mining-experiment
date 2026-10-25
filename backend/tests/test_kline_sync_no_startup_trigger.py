"""回归：K 线权威修正不得在容器启动时触发

历史问题：_post_deploy_reconcile_once 在每次容器重建（33 每次合并
master 自动部署）后全量跑 sync_kline_from_tickdata + 清 kline:history:*
缓存，给 vvtr 分发源与 PG 带来巨大压力。修正只允许在 kline_sync_loop
的每日 01:00 / 15:05 定时点执行（管理端手动触发除外）。
"""

from __future__ import annotations

import asyncio

import app.services.kline_sync as kline_sync
import app.services.kline_task_status as kts
import app.services.scheduler as scheduler


class _FakeRedis:
    async def scan_iter(self, pattern: str):
        return
        yield  # pragma: no cover - 生成器语法占位


async def test_post_deploy_reconcile_does_not_run_vvtr_sync(monkeypatch):
    """启动恢复只治愈 forming；不得触碰 vvtr 修正与历史缓存清理"""
    counters = {"sync": 0, "heal": 0, "cache_scan": 0}

    async def _must_not_sync(*args, **kwargs):
        counters["sync"] += 1
        raise AssertionError("K 线修正不得在启动触发")

    monkeypatch.setattr(kline_sync, "sync_kline_from_tickdata", _must_not_sync)

    async def _fake_heal(redis_conn):
        counters["heal"] += 1
        return 2

    monkeypatch.setattr(
        "app.services.kline.heal_recovered_forming", _fake_heal
    )

    # 跳过启动 120s 等待
    async def _no_sleep(delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    reports: list[tuple] = []

    async def _fake_report(redis, name, **kw):
        reports.append((name, kw))

    async def _fake_log(redis, name, level, msg, **kw):
        reports.append((name, msg))

    monkeypatch.setattr(kts, "report", _fake_report)
    monkeypatch.setattr(kts, "log", _fake_log)

    r = _FakeRedis()

    async def _tracked_scan(pattern):
        counters["cache_scan"] += 1
        return
        yield  # pragma: no cover

    r.scan_iter = _tracked_scan

    await scheduler._post_deploy_reconcile_once(r)

    assert counters["heal"] == 1  # 轻量恢复步骤保留
    assert counters["sync"] == 0  # 全量修正不再启动触发
    assert counters["cache_scan"] == 0  # 历史缓存不再被清
    # 任务状态照常上报（管理端可见）
    assert any(name == "post_deploy_reconcile" for name, _ in reports)
