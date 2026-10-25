"""ORM 基类 —— 供所有模型共享"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """ORM 声明式基类"""
    pass
