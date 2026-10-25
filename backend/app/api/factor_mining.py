"""超级因子挖掘 API —— 长程任务 CRUD + 进度查询"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.core.market_capability import require_futures_market
from app.models.user import User
from app.services.factor_mining import (
    cancel_task,
    create_task,
    delete_task,
    get_champions,
    get_task,
    list_tasks,
    pause_task,
    resume_task,
)
from app.services.kline_history import list_mining_symbols
from app.services.factor_mining.capabilities import checkpoint_capability, checkpoint_worker_connected

router = APIRouter(prefix="/api/factor-mining", tags=["超级因子挖掘"], dependencies=[Depends(require_futures_market)])


async def require_checkpoint_worker():
    if not checkpoint_worker_connected():
        raise HTTPException(503, checkpoint_capability()["reason"], headers={"X-Request-ID": uuid4().hex, "Cache-Control": "no-store", "X-Research-Error": "checkpoint_worker_not_connected"})


class CreateTaskRequest(BaseModel):
    """创建挖掘任务请求"""

    symbol: str
    timeframe: str = "1d"
    population: int = Field(40, ge=10, le=200)
    generations: int = Field(30, ge=3, le=200)
    max_depth: int = Field(4, ge=2, le=6)
    train_ratio: float = Field(0.7, ge=0.0, lt=0.95)
    walk_forward_folds: int = Field(3, ge=0, le=6)
    live_fill_gate: bool = False
    cross_validate: bool = False
    islands: int = Field(1, ge=1, le=8)
    seed_tokens: list[list[int]] | None = None
    device: str = Field("cpu", pattern="^(cpu|gpu)$")
    name: str = ""


@router.get("/supported")
async def api_supported(_user: User = Depends(get_current_user)) -> dict:
    """支持的周期与说明（前端据此区分日线超长/分钟近期）"""
    return {
        "timeframes": [
            {"value": "1d", "label": "日线", "long_history": False, "note": "须核实当前数据库覆盖、真实期货标识与固定输入"},
            {"value": "60m", "label": "60分", "long_history": False, "note": "须核实来源、登录盘与换月口径"},
            {"value": "30m", "label": "30分", "long_history": False, "note": "须核实来源、登录盘与换月口径"},
            {"value": "15m", "label": "15分", "long_history": False, "note": "须核实来源、登录盘与换月口径"},
            {"value": "5m", "label": "5分", "long_history": False, "note": "须核实来源、登录盘与换月口径"},
            {"value": "1m", "label": "1分", "long_history": False, "note": "须核实来源、登录盘与换月口径"},
        ],
        "device_options": [
            {"value": "cpu", "label": "CPU", "available": True},
            {"value": "gpu", "label": "GPU（未实现）", "available": False},
        ],
        "max_running_per_user": 2,
        **checkpoint_capability(),
    }


@router.get("/symbols")
async def api_symbols(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """可挖掘品种列表 —— PG 中有日线历史的品种（含数据范围与根数）

    下拉菜单的数据源。只有这里返回的品种能做超长挖掘（loader 直查 PG）。
    symbol/symbol_name 为主力合约口径（如 rb2701），与因子实验室品种选项
    同格式；无主力映射的品种为 null，前端回退品种 code。
    """
    items = await list_mining_symbols(session)
    return {"total": len(items), "items": items}


@router.post("/tasks", status_code=201, dependencies=[Depends(require_checkpoint_worker)])
async def api_create_task(
    body: CreateTaskRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """创建挖掘任务"""
    redis_conn = getattr(request.app.state, "redis", None)
    return await create_task(
        session,
        user.id,
        symbol=body.symbol,
        timeframe=body.timeframe,
        population=body.population,
        generations=body.generations,
        max_depth=body.max_depth,
        train_ratio=body.train_ratio,
        walk_forward_folds=body.walk_forward_folds,
        live_fill_gate=body.live_fill_gate,
        cross_validate=body.cross_validate,
        islands=body.islands,
        seed_tokens=body.seed_tokens,
        device=body.device,
        name=body.name,
        redis_conn=redis_conn,
    )


@router.get("/tasks", dependencies=[Depends(require_checkpoint_worker)])
async def api_list_tasks(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """任务列表"""
    items = await list_tasks(session, _user.id)
    return {"total": len(items), "items": items}


@router.get("/tasks/{task_id}", dependencies=[Depends(require_checkpoint_worker)])
async def api_get_task(
    task_id: UUID,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """任务详情（含进度）"""
    return await get_task(session, _user.id, task_id)


@router.post("/tasks/{task_id}/pause", dependencies=[Depends(require_checkpoint_worker)])
async def api_pause_task(
    task_id: UUID,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """暂停任务"""
    return await pause_task(session, _user.id, task_id)


@router.post("/tasks/{task_id}/resume", dependencies=[Depends(require_checkpoint_worker)])
async def api_resume_task(
    task_id: UUID,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """恢复任务"""
    return await resume_task(session, _user.id, task_id)


@router.post("/tasks/{task_id}/cancel", dependencies=[Depends(require_checkpoint_worker)])
async def api_cancel_task(
    task_id: UUID,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """取消任务"""
    return await cancel_task(session, _user.id, task_id)


@router.delete("/tasks/{task_id}", dependencies=[Depends(require_checkpoint_worker)])
async def api_delete_task(
    task_id: UUID,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """删除任务（仅非运行中）"""
    detail = await get_task(session, _user.id, task_id)
    if detail["status"] in ("running", "pending"):
        raise HTTPException(status_code=400, detail="请先取消或暂停任务再删除")
    await delete_task(session, _user.id, task_id)
    return {"ok": True}


@router.get("/tasks/{task_id}/champions", dependencies=[Depends(require_checkpoint_worker)])
async def api_get_champions(
    task_id: UUID,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """获取完成任务的冠军因子（含 train/test 双指标）"""
    items = await get_champions(session, _user.id, task_id)
    return {"total": len(items), "champions": items}
