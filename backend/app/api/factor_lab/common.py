"""因子实验室 API 公共工具 —— 周期校验、K 线加载、成本解析、编码守卫"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.factor_lab import kernel_version
from app.services.factor_lab.scoring.cost import (
    DEFAULT_SLIPPAGE_TICKS,
    turnover_cost_rate,
)
from app.services.factor_lab.token_encoding import looks_like_v1
from app.services.factor_lab.vm import FEAT_COUNT
from app.services.kline import get_kline_history

VALID_TF = ("1d", "60m", "30m", "15m", "5m", "1m")
TF_LIMIT = {
    "1d": 500,
    "60m": 1500,
    "30m": 2000,
    "15m": 3000,
    "5m": 4000,
    "1m": 6000,
}

LEGACY_TOKEN_DETAIL = (
    "该因子公式使用旧版 token 编码，直接执行会解码成不同的算子。"
    "请先运行 scripts/migrate-factor-token-encoding.py 迁移存量公式。"
)


def reject_legacy_tokens(tokens_list: list[list[int]]) -> None:
    """存量 token 若是旧编码则拒绝执行，避免静默解码成别的算子

    只做纯值域判断（不查库）：v2 下 [FEAT_COUNT, MAX_FEATURES) 是特征空间
    的未使用空洞，合法公式不会落在其中，而旧编码的算子 id 恰好在此。
    """
    for tokens in tokens_list:
        if looks_like_v1(tokens, FEAT_COUNT):
            raise HTTPException(status_code=409, detail=LEGACY_TOKEN_DETAIL)


def resolve_cost(
    symbol: str,
    bars: list[dict],
    cost: float | None,
) -> float:
    """解析单位 turnover 成本率

    显式传入则尊重用户；为 None 时按品种规格推导
    （手续费 + 1 个最小变动价位滑点）。价格取最近 25% bar 的中位数——
    用整段均值会把远端历史价格"未来化"地定成本（轻度前视），且对
    长区间品种（几十年价格漂移数倍）严重失真；近端中位数更接近
    实际将要支付的成本水平。
    """
    if cost is not None:
        return cost
    valid = [float(b.get("close") or 0) for b in bars]
    valid = [c for c in valid if c > 0]
    if not valid:
        return 0.0
    recent = valid[-max(60, len(valid) // 4):]
    price = sorted(recent)[len(recent) // 2]
    return turnover_cost_rate(symbol, price, DEFAULT_SLIPPAGE_TICKS)


async def load_bars(
    redis_conn: object | None,
    session: AsyncSession | None,
    symbol: str,
    timeframe: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """拉取 K 线（因子搜索不受 93 天限制）

    不传日期：近期数据（TF_LIMIT 根），与历史行为一致。
    传 start_date/end_date（YYYY-MM-DD，必须成对）：长历史区间模式，
    走 tickdata（pg-tickdata，2005 起，含主力连续拼接）。
    """
    sym = (symbol or "").strip().lower()
    if not sym:
        raise HTTPException(status_code=400, detail="请选择品种")
    if timeframe not in VALID_TF:
        raise HTTPException(status_code=400, detail="不支持的周期")
    if (start_date is None) != (end_date is None):
        raise HTTPException(
            status_code=400, detail="start_date 与 end_date 必须成对提供"
        )
    if start_date is None:
        bars = await get_kline_history(
            redis_conn,
            sym,
            timeframe,
            limit=TF_LIMIT[timeframe],
            end_time=None,
            session=session,
        )
    else:
        bars = await _load_ranged_bars(
            redis_conn, session, sym, timeframe, start_date, end_date
        )
    if not bars:
        raise HTTPException(status_code=400, detail="未获取到 K 线数据")
    return bars


# 因子评估专用区间上限（自然天）。不借用策略回测的 TIMEFRAME_MAX_DAYS：
# 那套分钟上限（15m=30 天）为策略运行时性能设计；因子评估是 numpy 向量化，
# 负担得起更长历史，而短区间（30 天 15m ≈ 480 根）连 walk-forward 每折
# MIN_TEST_BARS=30 都勉强，严格筛必然全军覆没（全部回退 ⚠）。
# 上限按「向量化评估的合理数据量」设定：1m 30 天 ≈ 7200 根封顶。
FACTOR_TF_MAX_DAYS: dict[str, int] = {
    "1d": 1825,
    "60m": 365,
    "30m": 240,
    "15m": 180,
    "5m": 90,
    "1m": 30,
}


async def _load_ranged_bars(
    redis_conn: object | None,
    session: AsyncSession | None,
    sym: str,
    timeframe: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """长历史区间模式：因子专用区间校验 + 策略回测同款 limit 推导

    与策略回测的差异：不保留 start 前的 warmup bar —— 因子口径是评估
    全部载入的 bar，区间头部特征窗口部分退化与近期模式一致。
    tickdata 未配置/未命中时 get_kline_history 自动降级（PG 日线/分钟），
    返回降级源实际拿到的区间内 bars，由响应 range 回显真实覆盖。
    """
    from datetime import datetime

    from app.services.backtest.data import parse_date

    try:
        start = parse_date(start_date)
        end = parse_date(end_date)
        if end < start:
            raise ValueError("结束日期不能早于开始日期")
        max_days = FACTOR_TF_MAX_DAYS.get(timeframe, 30)
        if (end - start).days > max_days:
            raise ValueError(f"{timeframe} 周期因子区间最多 {max_days} 天")
        if start > datetime.now().date():
            raise ValueError("开始日期不能晚于今天")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if timeframe == "1d":
        limit = int((end - start).days * 1.5) + 200
    else:
        per_day = {"1m": 240, "5m": 48, "15m": 16, "30m": 8, "60m": 4}.get(
            timeframe, 240
        )
        trading_days = max((end - start).days * 5 // 7, 1)
        limit = trading_days * per_day + 1000
    limit = max(limit, 500)

    bars = await get_kline_history(
        redis_conn,
        sym,
        timeframe,
        limit=limit,
        end_time=f"{end.isoformat()} 23:59:59",
        session=session,
        adjusted=True,  # 换月平滑：因子收益口径与真实换月一致
    )
    start_s = start.isoformat()
    end_s = end.isoformat()
    out = [
        b
        for b in bars
        if start_s <= str(b.get("time") or "")[:10] <= end_s
    ]
    if not out:
        raise HTTPException(
            status_code=400,
            detail="区间内未获取到 K 线数据（长历史库可能未配置或该品种无此区间数据）",
        )
    return out


def _round_metrics(metrics: dict) -> dict:
    """递归 round metrics，兼容嵌套的 train_metrics/test_metrics/walk_forward 子键

    原实现 {k: round(float(v),4)} 对 dict/list 值（如 walk_forward 折明细）
    会 float({...}) 崩溃。改为：标量直接 round，dict 递归，list 逐项递归。
    bool 保持原值（round(True)=1 会丢类型），None 透传。
    """
    out: dict = {}
    for k, v in metrics.items():
        out[k] = _round_value(v)
    return out


def _round_value(v):
    """_round_metrics 的单值版本"""
    if isinstance(v, bool):
        return v
    if isinstance(v, dict):
        return {kk: _round_value(vv) for kk, vv in v.items()}
    if isinstance(v, list):
        return [_round_value(x) for x in v]
    if isinstance(v, (int, float)):
        return round(float(v), 4)
    return v


def champion_payload(champions: list, stamp_kernel: bool = False) -> list[dict]:
    """Champion 对象/dict → API 列表

    stamp_kernel=True 时在 metrics 打当前内核口径版本戳（清单第 2 步：
    仅供"本内核刚产出"的路径用——搜索/LLM/挖掘）。透传路径（历史面板
    存桌面端本地结果、读取已存任务）必须保持 False：旧记录的既有戳或
    缺戳是 stale_kernel 判定的原料，不得覆盖或补造。
    """
    out: list[dict] = []
    for c in champions:
        if isinstance(c, dict):
            tokens = list(c.get("tokens") or [])
            text = str(c.get("text") or "")
            composite = float(c.get("composite") or 0.0)
            metrics = c.get("metrics") if isinstance(c.get("metrics"), dict) else {}
            metrics = dict(metrics)
        else:
            tokens = list(c.tokens)
            text = c.text
            composite = float(c.composite)
            metrics = dict(c.metrics or {})
        if stamp_kernel:
            metrics["kernel_version"] = kernel_version()
        out.append(
            {
                "tokens": tokens,
                "text": text,
                "composite": round(composite, 4),
                "metrics": _round_metrics(metrics),
            }
        )
    return out
