"""模拟交易工具执行 —— 下单/撤单/查询

与 UI 共用 trading_svc / order_query / account_svc。
写操作成功后显式 commit，避免仅 flush 未落库。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.paper.account_svc import get_account_summary
from app.services.paper.order_query import cancel_order, list_orders, list_positions
from app.services.paper.trading_svc import place_order


def _err(msg: str) -> dict[str, Any]:
    return {"success": False, "error": msg}


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"success": True}
    out.update(payload)
    return out


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(str(value).strip())
    except (TypeError, ValueError):
        return None


async def _commit_safe(session: AsyncSession) -> None:
    """写操作后提交；失败时回滚并抛出"""
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise


async def _place(
    session: AsyncSession,
    user_id: str,
    symbol: str,
    direction: str,
    offset: str,
    order_type: str,
    price: float | None,
    quantity: int,
    symbol_name: str,
    redis_conn: object | None,
) -> dict[str, Any]:
    """统一下单封装"""
    uid = _parse_uuid(user_id)
    if uid is None:
        return _err("无效用户 ID")
    sym = str(symbol or "").strip().lower()
    if not sym:
        return _err("合约代码不能为空")
    qty = _as_int(quantity, 0)
    if qty <= 0:
        return _err("手数须为正整数")
    ot = (order_type or "market").strip().lower()
    if ot not in ("market", "limit"):
        return _err("order_type 须为 market 或 limit")
    px = _as_float(price)
    if px is None or px <= 0:
        return _err("请提供有效价格")
    try:
        result = await place_order(
            session,
            uid,
            sym,
            direction,
            offset,
            ot,
            px,
            qty,
            symbol_name or "",
            None,
            None,
            redis_conn,
        )
        await _commit_safe(session)
        return _ok({"order": result, "message": result.get("message")})
    except HTTPException as exc:
        await session.rollback()
        return _err(str(exc.detail))
    except Exception as exc:
        await session.rollback()
        return _err(f"下单失败: {str(exc)[:200]}")


async def tool_paper_open_long(
    arguments: dict[str, Any],
    session: AsyncSession | None,
    user_id: str | None,
    redis_conn: object | None,
) -> dict[str, Any]:
    """做多开仓"""
    if session is None or user_id is None:
        return _err("需要数据库会话与用户")
    return await _place(
        session,
        user_id,
        str(arguments.get("symbol", "")),
        "buy",
        "open",
        str(arguments.get("order_type") or "market"),
        arguments.get("price"),
        _as_int(arguments.get("quantity"), 0),
        str(arguments.get("symbol_name") or ""),
        redis_conn,
    )


async def tool_paper_open_short(
    arguments: dict[str, Any],
    session: AsyncSession | None,
    user_id: str | None,
    redis_conn: object | None,
) -> dict[str, Any]:
    """做空开仓"""
    if session is None or user_id is None:
        return _err("需要数据库会话与用户")
    return await _place(
        session,
        user_id,
        str(arguments.get("symbol", "")),
        "sell",
        "open",
        str(arguments.get("order_type") or "market"),
        arguments.get("price"),
        _as_int(arguments.get("quantity"), 0),
        str(arguments.get("symbol_name") or ""),
        redis_conn,
    )


async def tool_paper_close_position(
    arguments: dict[str, Any],
    session: AsyncSession | None,
    user_id: str | None,
    redis_conn: object | None,
) -> dict[str, Any]:
    """平仓：long→卖平，short→买平"""
    if session is None or user_id is None:
        return _err("需要数据库会话与用户")
    pos_dir = str(arguments.get("position_direction") or "").strip().lower()
    if pos_dir not in ("long", "short"):
        return _err("position_direction 须为 long 或 short")
    direction = "sell" if pos_dir == "long" else "buy"
    return await _place(
        session,
        user_id,
        str(arguments.get("symbol", "")),
        direction,
        "close",
        str(arguments.get("order_type") or "market"),
        arguments.get("price"),
        _as_int(arguments.get("quantity"), 0),
        str(arguments.get("symbol_name") or ""),
        redis_conn,
    )


async def tool_paper_place_limit(
    arguments: dict[str, Any],
    session: AsyncSession | None,
    user_id: str | None,
    redis_conn: object | None,
) -> dict[str, Any]:
    """限价挂单"""
    if session is None or user_id is None:
        return _err("需要数据库会话与用户")
    direction = str(arguments.get("direction") or "").strip().lower()
    offset = str(arguments.get("offset") or "").strip().lower()
    if direction not in ("buy", "sell"):
        return _err("direction 须为 buy 或 sell")
    if offset not in ("open", "close"):
        return _err("offset 须为 open 或 close")
    return await _place(
        session,
        user_id,
        str(arguments.get("symbol", "")),
        direction,
        offset,
        "limit",
        arguments.get("price"),
        _as_int(arguments.get("quantity"), 0),
        str(arguments.get("symbol_name") or ""),
        redis_conn,
    )


async def tool_paper_cancel_order(
    arguments: dict[str, Any],
    session: AsyncSession | None,
    user_id: str | None,
) -> dict[str, Any]:
    """撤单"""
    if session is None or user_id is None:
        return _err("需要数据库会话与用户")
    uid = _parse_uuid(user_id)
    oid = _parse_uuid(str(arguments.get("order_id") or ""))
    if uid is None or oid is None:
        return _err("order_id 无效")
    try:
        result = await cancel_order(session, uid, oid)
        await _commit_safe(session)
        return _ok({"order": result})
    except HTTPException as exc:
        await session.rollback()
        return _err(str(exc.detail))
    except Exception as exc:
        await session.rollback()
        return _err(f"撤单失败: {str(exc)[:200]}")


async def tool_paper_list_positions(
    session: AsyncSession | None,
    user_id: str | None,
    *,
    trading_mode: str,
) -> dict[str, Any]:
    """持仓列表"""
    if session is None or user_id is None:
        return _err("需要数据库会话与用户")
    uid = _parse_uuid(user_id)
    if uid is None:
        return _err("无效用户")
    try:
        return _ok(await list_positions(session, uid, trading_mode=trading_mode))
    except Exception as exc:
        return _err(f"查询持仓失败: {str(exc)[:200]}")


async def tool_paper_list_orders(
    arguments: dict[str, Any],
    session: AsyncSession | None,
    user_id: str | None,
    *,
    trading_mode: str,
) -> dict[str, Any]:
    """委托列表"""
    if session is None or user_id is None:
        return _err("需要数据库会话与用户")
    uid = _parse_uuid(user_id)
    if uid is None:
        return _err("无效用户")
    status = arguments.get("status")
    status_f = str(status).strip() if status else None
    limit = _as_int(arguments.get("limit"), 20)
    limit = max(1, min(limit, 100))
    try:
        return _ok(await list_orders(session, uid, status_f, limit, 0, trading_mode=trading_mode))
    except Exception as exc:
        return _err(f"查询委托失败: {str(exc)[:200]}")


async def tool_paper_get_account(
    session: AsyncSession | None,
    user_id: str | None,
    *,
    trading_mode: str,
) -> dict[str, Any]:
    """账户概览"""
    if session is None or user_id is None:
        return _err("需要数据库会话与用户")
    uid = _parse_uuid(user_id)
    if uid is None:
        return _err("无效用户")
    try:
        summary = await get_account_summary(session, uid, trading_mode=trading_mode)
        return _ok({"account": summary})
    except Exception as exc:
        return _err(f"查询账户失败: {str(exc)[:200]}")
