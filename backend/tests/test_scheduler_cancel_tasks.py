"""scheduler 任务清理回归 —— 未启动任务时取消不得抛 UnboundLocalError

历史 bug：_cancel_data_tasks 引用了未列入 global 声明的新任务变量
（如 _vvtr_bridge_task），赋值语句使其成为局部变量，前置引用在
应用启动/锁丢失清理路径直接 UnboundLocalError 崩溃。
"""

import asyncio

import app.services.scheduler as scheduler


async def test_cancel_data_tasks_with_fresh_module_state():
    # 全部任务变量为初始 None 时清理应静默通过（启动/锁丢失路径）
    for name in (
        "_bg_task",
        "_news_task",
        "_master_task",
        "_ai_trading_task",
        "_virtual_side_task",
        "_price_alert_task",
        "_big_order_purge_task",
        "_channel_health_task",
        "_channel_preflight_task",
        "_kline_source_health_task",
        "_kline_sync_task",
        "_vvtr_bridge_task",
    ):
        assert hasattr(scheduler, name), f"缺少任务变量 {name}"

    await scheduler._cancel_data_tasks()  # 不应抛 UnboundLocalError
    await scheduler._cancel_data_tasks()  # 幂等


async def test_cancel_data_tasks_with_running_vvtr_task():
    async def _idle() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(_idle())
    scheduler._vvtr_bridge_task = task
    try:
        await scheduler._cancel_data_tasks()
        # 清理会取消任务并把模块变量置回 None
        assert scheduler._vvtr_bridge_task is None
        assert task.cancelled() or task.done()
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        scheduler._vvtr_bridge_task = None
