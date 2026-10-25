"""Redis 连接模块 —— 异步 Redis 连接"""

import asyncio
import logging

import redis.asyncio as aioredis
import redis.exceptions
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

from app.core.config import Settings

logger = logging.getLogger("qihuo.redis")

# 启动时连接重试：Redis 起晚/网络抖动（WSL 环境）时不至于让进程直接退出
_CONNECT_ATTEMPTS = 10
_CONNECT_BACKOFF = 2.0


async def create_redis_connection(settings: Settings) -> aioredis.Redis:
    """创建异步 Redis 连接。

    开启 health_check + retry_on_error：redis 抖动/短暂断连时由 redis-py 自动
    重连重试，避免 ConnectionError 冒泡到应用导致进程崩溃（曾引发 backend exit 128）。
    初始 ping 带有限重试，容忍 Redis 晚于后端就绪。
    """
    connection = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        max_connections=20,
        health_check_interval=30,
        retry=Retry(backoff=ExponentialBackoff(cap=1.0, base=0.1), retries=5),
        retry_on_error=[
            redis.exceptions.ConnectionError,
            redis.exceptions.TimeoutError,
        ],
    )
    # 验证连接（有限重试，应对启动竞态/网络抖动）
    last_err: Exception | None = None
    for attempt in range(_CONNECT_ATTEMPTS):
        try:
            await connection.ping()
            return connection
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
            last_err = exc
            logger.warning(
                "Redis 连接失败（%d/%d）：%s",
                attempt + 1,
                _CONNECT_ATTEMPTS,
                exc,
            )
            await asyncio.sleep(_CONNECT_BACKOFF)
    await connection.close()
    raise redis.exceptions.ConnectionError(
        f"Redis 连接重试 {_CONNECT_ATTEMPTS} 次后仍失败: {last_err}"
    )


async def close_redis_connection(connection: aioredis.Redis) -> None:
    """关闭 Redis 连接"""
    await connection.close()
