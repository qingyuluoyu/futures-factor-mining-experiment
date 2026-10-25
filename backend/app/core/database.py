"""数据库连接模块 —— SQLAlchemy asyncio 连接池"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.core.config import Settings


def create_database_engine(settings: Settings):
    """创建异步数据库引擎"""
    engine = create_async_engine(
        settings.database_url,
        pool_size=10,
        max_overflow=20,
        poolclass=AsyncAdaptedQueuePool,
        echo=settings.app_env == "development",
        hide_parameters=True,
    )
    return engine


def create_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """创建数据库会话工厂"""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
