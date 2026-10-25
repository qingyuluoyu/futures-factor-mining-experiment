from uuid import uuid4

from fastapi import Depends, HTTPException

from app.core.deps import get_current_user
from app.models.user import User


async def require_stock_market(user: User = Depends(get_current_user)) -> None:
    if getattr(user, "market_type", None) != "stock":
        raise HTTPException(409, "当前会话不在A股市场，请先切换市场", headers={"X-Market-Capability": "stock_required", "X-Request-ID": uuid4().hex, "Cache-Control": "no-store"})


async def require_futures_market(user: User = Depends(get_current_user)) -> None:
    if getattr(user, "market_type", None) != "futures":
        raise HTTPException(409, "当前会话不在期货市场，请使用A股独立回放账户", headers={"X-Market-Capability": "futures_required", "X-Request-ID": uuid4().hex, "Cache-Control": "no-store"})
