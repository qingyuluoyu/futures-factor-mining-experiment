"""AI 交易 API —— 任务生命周期、决策日志、收益曲线（按 JWT trading_mode 分盘）"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_auth_context, get_session
from app.core.financial_request_route import FinancialRequestRoute
from app.schemas.ai_trading import (
    CreateAITradingTaskRequest,
    EstimateCapitalRequest,
    SwitchModelRequest,
    UpdateAITradingTaskRequest,
    UpdateTaskRulesRequest,
)
from app.services.ai_trading import equity as equity_svc
from app.services.ai_trading.engine import evaluate_task
from app.services.ai_trading.task import history_svc, profit_svc, task_lifecycle, task_svc
from app.services.trading_hours import get_session_status

router = APIRouter(prefix="/api/ai-trading", tags=["AI交易"], route_class=FinancialRequestRoute)


def _require_market_open(symbol: str) -> None:
    """端点层防御：非交易时段拒绝会触发下单/评估的写操作。

    virtual 模式 get_session_status 恒返回 is_open=true，自动放行；
    仅 live 实盘在停盘期间拒绝。start_task 的 market_closed 挂起行为是产品
    有意设计，故该防御只用于 stop/run-once，不影响 start。
    """
    if not get_session_status(symbol).get("is_open"):
        raise HTTPException(
            status_code=400, detail="非交易时段，请在开盘期间操作"
        )


@router.get("/tasks")
async def list_tasks(
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """任务列表（含任务品种实时浮盈，不含手续费）"""
    redis_conn = getattr(request.app.state, "redis", None)
    items = await task_svc.list_tasks(
        session, ctx.user.id, redis_conn, ctx.trading_mode
    )
    return {"total": len(items), "items": items}


@router.post("/tasks", status_code=201)
async def create_task(
    body: CreateAITradingTaskRequest,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """创建 AI 交易任务"""
    from app.services.quota.quota_svc import (
        count_active_tasks,
        get_effective_limits,
    )

    # 任务数配额（admin 不限）
    limits = await get_effective_limits(session, ctx.user)
    if limits["max_tasks"] is not None:
        active = await count_active_tasks(session, ctx.user)
        if active >= int(limits["max_tasks"]):
            raise HTTPException(
                status_code=403,
                detail=f"任务数已达上限 {limits['max_tasks']} 个",
            )
    payload = body.model_dump()
    return await task_svc.create_task(
        session, ctx.user.id, payload, ctx.trading_mode
    )


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: UUID,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """任务详情"""
    redis_conn = getattr(request.app.state, "redis", None)
    _, data = await task_svc.get_task(
        session, ctx.user.id, task_id, redis_conn, ctx.trading_mode
    )
    return data


@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: UUID,
    body: UpdateAITradingTaskRequest,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """更新任务配置（仅已结束且无持仓/未下单）"""
    # 先校验任务属于当前盘
    await task_svc.get_task(
        session, ctx.user.id, task_id, trading_mode=ctx.trading_mode
    )
    payload = body.model_dump(exclude_unset=True)
    return await task_svc.update_task(
        session, ctx.user.id, task_id, payload
    )


@router.patch("/tasks/{task_id}/rules")
async def update_task_rules(
    task_id: UUID,
    body: UpdateTaskRulesRequest,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """调整止盈/止损规则（该品种无持仓即可，运行中下轮评估生效，不改任务状态）"""
    payload = body.model_dump(exclude_unset=True)
    return await task_svc.update_task_rules(
        session, ctx.user.id, task_id, payload, trading_mode=ctx.trading_mode
    )


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: UUID,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """删除已结束且无持仓的任务（级联删除决策/权益点，订单保留）"""
    return await task_svc.delete_task(
        session, ctx.user.id, task_id, ctx.trading_mode
    )


@router.post("/tasks/{task_id}/start")
async def start_task(
    task_id: UUID,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """开始/恢复"""
    return await task_lifecycle.start_task(
        session, ctx.user.id, task_id, ctx.trading_mode
    )


@router.post("/tasks/{task_id}/pause")
async def pause_task(
    task_id: UUID,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """暂停"""
    return await task_lifecycle.pause_task(
        session, ctx.user.id, task_id, ctx.trading_mode
    )


@router.post("/tasks/{task_id}/stop")
async def stop_task(
    task_id: UUID,
    request: Request,
    force_close: bool = Query(default=True, description="是否强制平该品种仓"),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """结束任务

    结束任务是用户随时应能执行的基本操作（尤其停盘时更想结束清理），
    不受交易时段限制。force_close 的平仓动作走 paper 层，paper 层有自己的
    开盘校验——停盘时平仓会失败但不阻塞任务状态变更为 stopped，
    用户可先结束任务、待开盘再平仓，或直接删除无持仓的任务。
    （修复死锁：原 _require_market_open 在停盘拒绝 stop，导致任务卡在
    running/paused 既删不掉（delete 要求 stopped）又停不掉，形成脏数据。）
    """
    redis_conn = getattr(request.app.state, "redis", None)
    return await task_lifecycle.stop_task(
        session,
        ctx.user.id,
        task_id,
        force_close,
        redis_conn,
        ctx.trading_mode,
    )


@router.get("/tasks/{task_id}/decisions")
async def list_decisions(
    task_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """分析记录"""
    await task_svc.get_task(
        session, ctx.user.id, task_id, trading_mode=ctx.trading_mode
    )
    return await history_svc.list_decisions(
        session, ctx.user.id, task_id, limit, offset
    )


@router.get("/tasks/{task_id}/trades")
async def list_trades(
    task_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """交易记录（关联 paper 委托）"""
    await task_svc.get_task(
        session, ctx.user.id, task_id, trading_mode=ctx.trading_mode
    )
    return await history_svc.list_task_trades(
        session, ctx.user.id, task_id, limit, offset
    )


@router.get("/tasks/{task_id}/trade-marks")
async def list_trade_marks(
    task_id: UUID,
    limit: int = Query(default=1000, ge=1, le=1000),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """任务 K 线交易标记（按 task_id 直查已成交委托，升序）"""
    await task_svc.get_task(
        session, ctx.user.id, task_id, trading_mode=ctx.trading_mode
    )
    return await history_svc.list_task_trade_marks(
        session, ctx.user.id, task_id, limit
    )


@router.post("/tasks/{task_id}/run-once")
async def run_once(
    task_id: UUID,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """调试：立即评估一次（忽略 bar 对齐，停盘期间拒绝）"""
    redis_conn = getattr(request.app.state, "redis", None)
    task, _ = await task_svc.get_task(
        session, ctx.user.id, task_id, redis_conn, ctx.trading_mode
    )
    if task.status == "stopped":
        raise HTTPException(status_code=400, detail="已结束任务不可评估")
    # 端点层防御：停盘期间禁止评估（会触发下单）
    _require_market_open(str(task.symbol or ""))
    result = await evaluate_task(session, task, redis_conn, force=True)
    return result


@router.post("/tasks/{task_id}/switch-model")
async def switch_model(
    task_id: UUID,
    body: SwitchModelRequest,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """切换任务模型并立即用新模型执行一次（接手操盘）。

    running/paused/stopped 均可，持仓/资金/策略规则不变，仅替换 AI 模型。
    """
    redis_conn = getattr(request.app.state, "redis", None)
    return await task_svc.switch_task_model(
        session,
        ctx.user.id,
        task_id,
        body.model_row_id,
        redis_conn,
        ctx.trading_mode,
    )


@router.get("/tasks/{task_id}/run-logs")
async def list_task_run_logs(
    task_id: UUID,
    level: str | None = Query(default=None),
    event: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """任务运行日志（信号/下单/异常事件流）"""
    await task_svc.get_task(
        session, ctx.user.id, task_id, trading_mode=ctx.trading_mode
    )
    from app.services.ai_trading.task.run_log_svc import list_run_logs

    rows, total = await list_run_logs(
        session, task_id=task_id, level=level, event=event,
        limit=limit, offset=offset,
    )
    return {"total": total, "items": rows}


@router.get("/tasks/{task_id}/run-logs/export")
async def export_task_run_logs(
    task_id: UUID,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    level: str | None = Query(default=None),
    event: str | None = Query(default=None),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """导出自己的任务运行日志（CSV/JSON）"""
    # StreamingResponse 必须模块级导入：本文件有 from __future__ import annotations，
    # 函数内导入会让返回注解解析成 ForwardRef，拖垮 /openapi.json 生成(全局 500)
    await task_svc.get_task(
        session, ctx.user.id, task_id, trading_mode=ctx.trading_mode
    )
    from app.services.ai_trading.task.run_log_export import (
        fetch_export_rows,
        rows_to_csv,
        rows_to_json,
    )

    rows = await fetch_export_rows(
        session, user_id=ctx.user.id, task_id=task_id, level=level,
        event=event, trading_mode=ctx.trading_mode,
    )
    stamp = __import__("datetime").datetime.utcnow().strftime("%Y%m%d_%H%M")
    if format == "json":
        return StreamingResponse(
            iter([rows_to_json(rows)]),
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="task_{task_id.hex[:8]}_logs_{stamp}.json"'
                )
            },
        )
    return StreamingResponse(
        iter(["﻿" + rows_to_csv(rows)]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="task_{task_id.hex[:8]}_logs_{stamp}.csv"'
            )
        },
    )


@router.get("/equity")
async def equity_series(
    task_ids: str = Query(default="", description="逗号分隔的任务 ID"),
    limit: int = Query(default=500, ge=10, le=2000),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """多任务权益曲线"""
    ids: list[UUID] = []
    for part in task_ids.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(UUID(part))
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"无效 task_id: {part}"
            ) from exc
    if not ids:
        tasks = await task_svc.list_tasks(
            session, ctx.user.id, trading_mode=ctx.trading_mode
        )
        ids = [UUID(t["id"]) for t in tasks]
    series = await equity_svc.list_equity_series(
        session, ctx.user.id, ids, limit
    )
    return {"series": series}


@router.get("/profit-bars")
async def profit_bars(
    request: Request,
    limit: int = Query(default=500, ge=10, le=2000),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """总收益柱：平仓累计 + 当前持仓浮盈（Redis 最新价）"""
    redis_conn = getattr(request.app.state, "redis", None)
    return await profit_svc.list_profit_close_bars(
        session, ctx.user.id, ctx.trading_mode, limit, redis_conn
    )


@router.post("/estimate-capital")
async def estimate_capital(
    body: EstimateCapitalRequest,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
) -> dict:
    """估算给定手数开仓所需 AI 资金仓额度（保证金 + 手续费 + 2% 缓冲）"""
    from app.services.ai_trading.core.execute import _estimate_open_need
    from app.services.paper.price_band import resolve_last_price
    from app.services.paper.specs import resolve_trade_params

    sym = (body.symbol or "").strip().lower()
    if not sym:
        raise HTTPException(status_code=400, detail="请输入合约代码")
    redis_conn = getattr(request.app.state, "redis", None)
    last_ref = await resolve_last_price(sym, redis_conn, None, allow_mock=False)
    params = resolve_trade_params(sym, None)
    last = float(last_ref) if last_ref else 0.0
    out: dict = {
        "symbol": sym,
        "last_price": round(last, 4),
        "multiplier": int(params.get("multiplier") or 10),
        "margin_rate": float(params.get("margin_rate") or 0.1),
        "fee_mode": str(params.get("fee_mode") or "rate"),
        "per_hand_margin": None,
        "total_needed": None,
    }
    if last > 0:
        per_hand = float(_estimate_open_need(sym, last, 1))
        total = float(_estimate_open_need(sym, last, max(1, body.qty)))
        out["per_hand_margin"] = round(per_hand, 2)
        out["total_needed"] = round(total, 2)
    return out

