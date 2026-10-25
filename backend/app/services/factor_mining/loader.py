"""超级因子挖掘 —— 超长历史数据加载

日线：直调 kline_history.query_history_bars，绕过 factor_lab 的 TF_LIMIT(500)，
      拿 PG 全量历史（可到 2000 年）。前提：该品种已跑过 import-kline-history.py。
分钟线：复用 get_kline_history（新浪近期，约 1-2 个月），非"超长"，调用方需在 UI 标注。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.contracts import get_code_for_symbol
from app.services.kline import get_kline_history
from app.services.kline_history import get_latest_history_date, query_history_bars

logger = logging.getLogger("qihuo.factor_mining")

# 超长挖掘的根数上限（防止内存爆炸：几千根日线 × 大种群已足够）
MAX_DAILY_BARS = 8000
# 分钟线上限：E5 实验（docs/experiments/factor-improvements-RESULTS.md P5 节）
# 深历史 2 万根（×6.7）下默认预算（40×20）严格筛冠军 6→0——长历史让
# train/test+WF 三重门极难全过；ma 因数据空洞截断仅 ×1.6。深度可用但
# 质量不升，按预登记规则回滚上限；tickdata 深分钟数据经换月平滑后仍可
# 通过 fetch_tickdata_kline(limit=..., adjusted=True) 直接取用。
MINUTE_TF_LIMIT = {
    "60m": 1500,
    "30m": 2000,
    "15m": 3000,
    "5m": 4000,
    "1m": 6000,
}


async def load_mining_bars(
    redis_conn: object | None,
    session: AsyncSession,
    symbol: str,
    timeframe: str,
) -> tuple[list[dict[str, Any]], str, str]:
    """加载挖掘用的超长历史 K 线

    返回 (bars, range_from, range_to)。
    - 日线：从 PG kline_history 全量拉取（最多 MAX_DAILY_BARS 根），返回真实数据范围。
    - 分钟线：复用新浪近期数据，range 标注为"近期"。

    若日线品种在 PG 无历史（未导入），抛 HTTPException 400 提示先跑导入脚本。
    """
    from fastapi import HTTPException

    sym = (symbol or "").strip().lower()
    if not sym:
        raise HTTPException(status_code=400, detail="请选择品种")

    if timeframe == "1d":
        return await _load_daily(session, sym)

    if timeframe in MINUTE_TF_LIMIT:
        return await _load_minute(redis_conn, session, sym, timeframe)

    raise HTTPException(status_code=400, detail=f"不支持的周期: {timeframe}")


async def _load_daily(
    session: AsyncSession,
    symbol: str,
) -> tuple[list[dict[str, Any]], str, str]:
    """日线：直调 PG，绕过 TF_LIMIT"""
    from fastapi import HTTPException

    code = get_code_for_symbol(symbol)
    if not code:
        raise HTTPException(status_code=400, detail=f"无法识别品种代码: {symbol}")

    # end_date 用 PG 中该品种最新日期；若无则用今天
    latest = await get_latest_history_date(session, code)
    if not latest:
        raise HTTPException(
            status_code=400,
            detail=(
                f"品种 {symbol}（{code}）在历史库中无日线数据，"
                f"请先在服务器运行: python scripts/import-kline-history.py --code {code}"
            ),
        )

    bars = await query_history_bars(session, code, latest, limit=MAX_DAILY_BARS)
    if not bars:
        raise HTTPException(status_code=400, detail=f"品种 {symbol} 历史日线数据为空")

    range_from = str(bars[0].get("time") or "")
    range_to = str(bars[-1].get("time") or "")
    logger.info("超长日线加载 %s: %d 根 (%s ~ %s)", symbol, len(bars), range_from, range_to)
    return bars, range_from, range_to


async def _load_minute(
    redis_conn: object | None,
    session: AsyncSession,
    symbol: str,
    timeframe: str,
) -> tuple[list[dict[str, Any]], str, str]:
    """分钟线：新浪近期（新鲜度最好）+ tickdata 具体合约深历史（前置补深）。

    新浪接口需要具体月份合约符号（如 rb2610），不认纯品种代码 rb，
    且只有 ~1000 根；tickdata 存具体合约全历史（无换月缺口），
    按 time 去重合并（新浪覆盖重叠段，保证最新 bar 新鲜）。
    """
    from fastapi import HTTPException

    from app.data.contracts import ALL_CONTRACTS, get_code_for_symbol

    limit = MINUTE_TF_LIMIT[timeframe]
    # 品种 code → 主力合约 symbol（ALL_CONTRACTS 里该 code 的第一个 symbol）
    code = get_code_for_symbol(symbol)
    contract_symbol = symbol  # 兜底：用户可能直接传了 rb2610
    if code:
        for c in ALL_CONTRACTS:
            if c.get("code") == code and c.get("symbol"):
                contract_symbol = c["symbol"]
                break
    bars = await get_kline_history(
        redis_conn,
        contract_symbol,
        timeframe,
        limit=limit,
        end_time=None,
        session=session,
    )
    td_bars = await _tickdata_minute_bars(contract_symbol, timeframe, limit)
    if not td_bars:
        # R2 发现：解析到的当期合约可能已过期（如 cu2607 停在 2026-07-15，
        # 新鲜度守卫整体丢弃）。两重回退：① 静态清单同品种其它合约；
        # ② tickdata 里"最近 30 天仍有数据"的合约（静态清单可能整条过期，
        # 每品种只存一条）。仍空才降级新浪。
        candidates = [
            c["symbol"]
            for c in ALL_CONTRACTS
            if c.get("code") == code
            and c.get("symbol")
            and c["symbol"] != contract_symbol
        ]
        try:
            from app.data.tickdata_kline import freshest_contracts

            fresh = await freshest_contracts(
                (code or symbol).lower(), timeframe, k=3
            )
            candidates += [s for s in fresh if s not in candidates]
        except Exception:
            logger.debug("freshest_contracts 回退失败", exc_info=True)
        for cand in candidates:
            try:
                td_bars = await _tickdata_minute_bars(cand, timeframe, limit)
            except Exception:
                td_bars = []
            if td_bars:
                logger.info(
                    "当期合约 %s 无 tickdata 分钟数据，改用 %s",
                    contract_symbol, cand,
                )
                break
    bars = _merge_minute_bars(bars, td_bars, limit)
    if not bars:
        raise HTTPException(
            status_code=400,
            detail=(
                f"品种 {symbol} 未获取到 {timeframe} K线数据，请稍后重试"
                "（行情数据可能在同步中）。"
            ),
        )
    range_from = str(bars[0].get("time") or "")
    range_to = str(bars[-1].get("time") or "")
    logger.info(
        "分钟线加载 %s(%s) %s: %d 根（近期+长历史库合并）",
        symbol, contract_symbol, timeframe, len(bars),
    )
    return bars, range_from, range_to


async def _tickdata_minute_bars(
    contract_symbol: str,
    timeframe: str,
    limit: int,
) -> list[dict[str, Any]]:
    """tickdata 具体合约分钟深历史；未配置/失败返回空（降级新浪）

    adjusted=True：分期主力拼接段做换月后向平滑——挖掘评估是
    close-to-close 口径，换月跳空会注入虚假盈亏污染适应度。
    """
    try:
        from app.data.tickdata_kline import fetch_tickdata_kline

        return await fetch_tickdata_kline(
            contract_symbol, timeframe, end_time="", limit=limit, adjusted=True
        )
    except Exception:
        logger.debug("tickdata 分钟深历史获取失败，降级新浪近期", exc_info=True)
        return []


def _drop_big_gaps(
    bars: list[dict[str, Any]], max_gap_days: int = 15
) -> list[dict[str, Any]]:
    """截断序列中段大空洞：丢弃最后一个 >max_gap_days 空洞之前的全部数据。

    tickdata 上游批次覆盖不齐时，拼接序列可能出现数月中段空洞（实测
    ma 15m 2025-07~10 缺主力合约数据）——空洞边缘的跳变会以假行情进入
    挖掘适应度。保留"最新连续段"（而非最长段）：挖掘要贴近当前市况，
    保长弃新等于用过期 regime 挖因子（E5 实测该错误使 ma 挖掘落在
    14 个月前的数据上）。
    """
    if len(bars) < 2:
        return bars
    from datetime import datetime

    def _dt(s: str):
        try:
            return datetime.fromisoformat(str(s)[:19])
        except ValueError:
            return None

    cut = 0
    for i in range(1, len(bars)):
        a, b = _dt(bars[i - 1].get("time") or ""), _dt(bars[i].get("time") or "")
        if a is None or b is None or (b - a).total_seconds() > max_gap_days * 86400:
            cut = i
    if cut == 0:
        return bars
    dropped = cut
    if dropped:
        logger.info(
            "分钟序列存在大空洞，截断至最新连续段 %d 根（丢弃空洞前 %d 根，"
            "新起点 %s）",
            len(bars) - dropped,
            dropped,
            bars[cut].get("time"),
        )
    return bars[cut:]


def _merge_minute_bars(
    sina_bars: list[dict[str, Any]],
    td_bars: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """新浪近期 + tickdata 深历史合并：同 time 键新浪优先，升序取尾部 limit 根"""
    if not td_bars:
        return sina_bars
    seen = {str(b.get("time") or "") for b in sina_bars}
    merged = [b for b in td_bars if str(b.get("time") or "") not in seen]
    merged = merged + list(sina_bars)
    merged.sort(key=lambda b: str(b.get("time") or ""))
    merged = merged[-limit:] if limit > 0 else merged
    return _drop_big_gaps(merged)
