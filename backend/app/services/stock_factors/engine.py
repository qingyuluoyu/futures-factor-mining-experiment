"""A 股因子引擎 —— 面板构建 / 预处理 / 因子检验 / 组合回测

题目 2 核心实现：
- 面板：价值/成长/质量/动量/波动/流动性因子，横截面（日期 × 股票）
- 前视偏差防护：财务因子按公告日（publish_date）对齐，只用决策日已披露的财报；
  量价因子只使用 T 日及之前数据
- 预处理：MAD/3σ 去极值、Z-Score 标准化、缺失值填充、行业/市值中性化
- 检验：IC（Pearson/Spearman）、IC IR、IC 衰减曲线、分组收益、多空净值
- 组合研究：上一交易日信号、当日收盘假设执行、逐日持仓估值及自融资成本
- 本地前复权样本仅用于研究，未验证真实撮合、公司行动及历史数据修订
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from app.services.stock_factors.catalog import FACTOR_BY_ID, factor_policy
from app.services.stock_factors.financial_selection import FinancialSelectionError, select_financial_rows

PERIODS_PER_YEAR = 244
METHOD_VERSION = "stock-research-2026-10-19.v5"
VALIDATION_LIMITS = (
    "本地前复权日线为研究口径，不是不复权回放撮合或可实盘收益；复权版本未留存。",
    "停牌、涨跌停、流动性、成交量限制、整手与真实成交均未验证；持仓缺价直接拒绝计算。",
    "公司行动、分红现金流、历史财报修订版本和历史成分股未完整建模，存在幸存者与修订偏差。",
    "仅用已覆盖股票和观测交易日；缺失整日行情与交易日历完整性未验证。",
    "手续费与滑点按成交额单边比例计费，最低佣金、印花税及冲击成本未建模。",
    "未进行基准比较、样本外、压力或容量验证；结果仅供研究，策略状态为待验证。",
)


class ResearchDataError(ValueError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


def _method_metadata() -> dict[str, Any]:
    return {
        "method_version": METHOD_VERSION,
        "validation_status": "research_only",
        "validation_limits": list(VALIDATION_LIMITS),
        "neutralization_policy": {"version": "neutralization-identifiability-v1", "min_industry_observations": 2, "min_residual_degrees_of_freedom": 1, "requires_full_column_rank": True, "insufficient_sample": "exclude_without_imputation"},
    }


def _research_error(code: str, message: str, status: int = 422) -> dict[str, Any]:
    return {"error": message, "error_code": code, "status_code": status, **_method_metadata()}


def _validated_close(data: MarketData) -> pd.DataFrame:
    close = data.close
    if close.empty:
        raise ResearchDataError("missing_market_data", "无行情数据，请先导入 A 股数据", 404)
    if not isinstance(close.index, pd.DatetimeIndex) or not close.index.is_unique or close.index.hasnans:
        raise ResearchDataError("invalid_market_data", "行情日期缺失或重复")
    if not close.columns.is_unique:
        raise ResearchDataError("invalid_market_data", "行情证券代码重复")
    try:
        close = close.astype(float).sort_index()
    except (ValueError, TypeError) as error:
        raise ResearchDataError("invalid_market_data", "行情价格类型错误") from error
    invalid = close.notna() & (~np.isfinite(close) | (close <= 0))
    if invalid.any().any():
        raise ResearchDataError("invalid_market_data", "行情包含非有限或非正价格")
    return close


# ---------------------------------------------------------------- 数据加载

@dataclass
class MarketData:
    """一次回测/检验所需的原始数据（内存面板）"""

    close: pd.DataFrame  # index=trade_date, columns=code
    valuation: dict[str, pd.DataFrame]  # factor_id -> (date × code)
    financial: dict[str, pd.DataFrame]  # factor_id -> (date × code)，已按公告日对齐
    industry: dict[str, str]  # code -> industry
    name: dict[str, str]  # code -> name


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame()


async def load_market_data(
    session_factory,
    start_date: date,
    end_date: date,
) -> MarketData:
    """加载股票面板：DB 拉取在请求事件循环，pandas 计算放线程池"""
    import asyncio

    from sqlalchemy import select

    from app.models.kline_history import KlineHistory
    from app.models.stock import StockFinancial, StockInstrument, StockValuation

    async with session_factory() as session:
        kline_rows = (
            (
                await session.execute(
                    select(
                        KlineHistory.code,
                        KlineHistory.trade_date,
                        KlineHistory.close,
                    )
                    .where(
                        KlineHistory.trade_date >= start_date,
                        KlineHistory.trade_date <= end_date,
                        KlineHistory.market_type == "stock",
                    )
                    .order_by(KlineHistory.trade_date)
                )
            )
            .all()
        )
        val_rows = (
            (
                await session.execute(
                    select(
                        StockValuation.code,
                        StockValuation.trade_date,
                        StockValuation.pe_ttm,
                        StockValuation.pb,
                        StockValuation.turnover_rate,
                        StockValuation.total_mv,
                    ).where(
                        StockValuation.trade_date >= start_date,
                        StockValuation.trade_date <= end_date,
                    )
                )
            )
            .all()
        )
        fin_rows = (
            (
                await session.execute(
                    select(
                        StockFinancial.code,
                        StockFinancial.report_date,
                        StockFinancial.publish_date,
                        StockFinancial.roe,
                        StockFinancial.gross_margin,
                        StockFinancial.revenue_yoy,
                        StockFinancial.netprofit_yoy,
                    )
                )
            )
            .all()
        )
        instruments = (await session.execute(select(StockInstrument))).scalars().all()

    return await asyncio.to_thread(
        _build_market_data, kline_rows, val_rows, fin_rows, instruments
    )


def _build_market_data(kline_rows, val_rows, fin_rows, instruments, *, valuation_fields=("pe_ttm", "pb", "turnover_rate", "total_mv"), financial_fields=("roe", "gross_margin", "revenue_yoy", "netprofit_yoy")) -> MarketData:
    """组装面板；公告只有日期，财务数据从公告日后的观测交易日可用。"""

    # ---- K线收盘价矩阵 ----
    close = pd.DataFrame(
        [(r[0], r[1], r[2]) for r in kline_rows], columns=["code", "date", "close"]
    )
    if close.empty:
        empty = _empty_frame()
        return MarketData(empty, {}, {}, {}, {})
    close = close.pivot(index="date", columns="code", values="close").sort_index()
    close.index = pd.to_datetime(close.index)

    # ---- 估值因子矩阵 ----
    val_df = pd.DataFrame(
        [
            (r[0], r[1], r[2], r[3], r[4], r[5])
            for r in val_rows
        ],
        columns=["code", "date", "pe_ttm", "pb", "turnover_rate", "total_mv"],
    )
    valuation: dict[str, pd.DataFrame] = {}
    if not val_df.empty:
        for col in valuation_fields:
            valuation[col] = val_df.pivot(index="date", columns="code", values=col).sort_index()
            valuation[col].index = pd.to_datetime(valuation[col].index)

    # ---- 财务因子矩阵（公告日对齐，防前视偏差）----
    try:
        selected_financial_rows = select_financial_rows(fin_rows)
    except FinancialSelectionError as error:
        raise ResearchDataError("invalid_market_data", str(error)) from error
    fin_df = pd.DataFrame(
        [
            (r[0], r[2], r[3], r[4], r[5], r[6])
            for r in selected_financial_rows
        ],
        columns=["code", "publish_date", "roe", "gross_margin", "revenue_yoy", "netprofit_yoy"],
    )
    financial: dict[str, pd.DataFrame] = {}
    if not fin_df.empty:
        fin_df["publish_date"] = pd.to_datetime(fin_df["publish_date"])
        trading_dates = pd.to_datetime(close.index)
        for col in financial_fields:
            matrices: dict[str, pd.Series] = {}
            for code, grp in fin_df.groupby("code"):
                grp = grp.sort_values("publish_date")
                pub = grp["publish_date"].values
                values = grp[col].values.astype(float)
                idx = np.searchsorted(pub, trading_dates.values, side="left") - 1
                series = np.where(idx >= 0, values[np.clip(idx, 0, None)], np.nan)
                series[idx < 0] = np.nan
                matrices[code] = pd.Series(series, index=close.index)
            financial[col] = pd.DataFrame(matrices).reindex(columns=close.columns)

    industry = {i.code: (i.industry or "") for i in instruments}
    name = {i.code: (i.name or i.code) for i in instruments}
    return MarketData(close, valuation, financial, industry, name)


# ---------------------------------------------------------------- 因子计算

def build_factor_matrix(data: MarketData, factor_id: str) -> pd.DataFrame:
    """单个因子的横截面矩阵（date × code）"""
    close = data.close
    if factor_id in ("pe_ttm", "pb", "turnover_rate"):
        return data.valuation.get(factor_id, _empty_frame())
    if factor_id in ("roe", "gross_margin", "revenue_yoy", "netprofit_yoy"):
        return data.financial.get(factor_id, _empty_frame())
    if factor_id == "ret_20d":
        return close / close.shift(20) - 1
    if factor_id == "vol_20d":
        rets = close.pct_change(fill_method=None)
        return rets.rolling(20).std() * math.sqrt(PERIODS_PER_YEAR)
    if factor_id == "turnover_20d":
        base = data.valuation.get("turnover_rate")
        if base is None or base.empty:
            return _empty_frame()
        return base.rolling(20).mean()
    raise ValueError(f"未知因子: {factor_id}")


def forward_return(data: MarketData, horizon: int = 20) -> pd.DataFrame:
    """信号后下一观测日收盘入场，再持有 horizon 个观测交易日。"""
    complete = data.close.notna().rolling(horizon + 1).sum().shift(-(horizon + 1))
    returns = data.close.shift(-(horizon + 1)) / data.close.shift(-1) - 1
    return returns.where(complete == horizon + 1)


# ---------------------------------------------------------------- 预处理

def winsorize_mad(frame: pd.DataFrame, n: float = 5.0) -> pd.DataFrame:
    """截面 MAD 去极值：median ± n × 1.4826 × MAD"""
    med = frame.median(axis=1)
    mad = (frame.sub(med, axis=0)).abs().median(axis=1) * 1.4826
    upper = med + n * mad
    lower = med - n * mad
    return frame.clip(lower=lower, upper=upper, axis=0)


def winsorize_3sigma(frame: pd.DataFrame) -> pd.DataFrame:
    """截面 3σ 去极值"""
    mean = frame.mean(axis=1)
    std = frame.std(axis=1)
    return frame.clip(lower=mean - 3 * std, upper=mean + 3 * std, axis=0)


def zscore(frame: pd.DataFrame) -> pd.DataFrame:
    """截面 Z-Score 标准化"""
    mean = frame.mean(axis=1)
    std = frame.std(axis=1).where(lambda values: values > 1e-12)
    return frame.sub(mean, axis=0).div(std, axis=0)


def fillna_median(frame: pd.DataFrame) -> pd.DataFrame:
    """截面中位数填充"""
    med = frame.median(axis=1)
    return frame.T.fillna(med).T


def neutralize_industry(frame: pd.DataFrame, industry: dict[str, str]) -> pd.DataFrame:
    """行业中性化：减去同行业截面均值（行业哑变量回归的等价简化）"""
    codes = list(frame.columns)
    groups = pd.Series([industry.get(c, "") for c in codes], index=codes)
    out = frame.copy()
    for ind, members in groups.groupby(groups).groups.items():
        cols = list(members)
        if len(cols) == 0:
            continue
        sub = frame[cols]
        out[cols] = sub.sub(sub.mean(axis=1), axis=0).where(sub.notna().sum(axis=1) >= 2, axis=0)
    return out


def neutralize_mktcap(frame: pd.DataFrame, mktcap: pd.DataFrame) -> pd.DataFrame:
    """在因子与正市值共同有效样本上，回归截距与 log(总市值)。"""
    return _neutralize_joint(frame, mktcap)


def _neutralize_joint(
    frame: pd.DataFrame,
    mktcap: pd.DataFrame,
    industry: dict[str, str] | None = None,
) -> pd.DataFrame:
    log_caps = np.log(mktcap.reindex_like(frame).where(lambda values: values > 0))
    out = pd.DataFrame(np.nan, index=frame.index, columns=frame.columns)
    categories = pd.Series(industry or {}, dtype=object).reindex(frame.columns)
    for trading_date in frame.index:
        valid = np.isfinite(frame.loc[trading_date]) & np.isfinite(log_caps.loc[trading_date])
        if industry is not None:
            valid &= categories.notna() & categories.ne("")
            counts = categories[valid].value_counts()
            valid &= categories.map(counts).fillna(0).ge(2)
        codes = frame.columns[valid]
        if len(codes) == 0:
            continue
        intercepts = (
            pd.get_dummies(categories.loc[codes], dtype=float).to_numpy()
            if industry is not None else np.ones((len(codes), 1))
        )
        design = np.column_stack([intercepts, log_caps.loc[trading_date, codes].to_numpy(dtype=float)])
        observed = frame.loc[trading_date, codes].to_numpy(dtype=float)
        rank = np.linalg.matrix_rank(design)
        if rank < design.shape[1] or len(codes) <= rank:
            continue
        coefficients = np.linalg.lstsq(design, observed, rcond=None)[0]
        out.loc[trading_date, codes] = observed - design @ coefficients
    return out


@dataclass
class PreprocessConfig:
    winsorize: str = "mad"  # mad / 3sigma / none
    standardize: bool = True
    fillna: bool = True
    industry_neutral: bool = False
    mktcap_neutral: bool = False

    def __post_init__(self):
        if self.winsorize not in {"mad", "3sigma", "none"}:
            raise ValueError("不支持的去极值方法")


def preprocess(
    frame: pd.DataFrame,
    config: PreprocessConfig,
    industry: dict[str, str] | None = None,
    mktcap: pd.DataFrame | None = None,
    *,
    eligible: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """按配置执行预处理链"""
    out = frame.replace([np.inf, -np.inf], np.nan)
    if eligible is not None:
        out = out.where(eligible)
    if config.winsorize == "mad":
        out = winsorize_mad(out)
    elif config.winsorize == "3sigma":
        out = winsorize_3sigma(out)
    if config.fillna:
        out = fillna_median(out)
    if eligible is not None:
        out = out.where(eligible)
    if config.industry_neutral and not industry:
        raise ResearchDataError("missing_industry", "行业中性化缺少行业分类数据")
    if config.mktcap_neutral and (mktcap is None or mktcap.empty):
        raise ResearchDataError("missing_market_cap", "市值中性化缺少市值数据")
    if config.mktcap_neutral:
        out = _neutralize_joint(out, mktcap, industry if config.industry_neutral else None)
    elif config.industry_neutral:
        classified = [code for code in out.columns if industry.get(code)]
        out = neutralize_industry(out[classified], industry).reindex(columns=out.columns)
    if config.standardize:
        out = zscore(out)
    return out


def _prepare_factor(
    data: MarketData,
    factor_id: str,
    config: PreprocessConfig,
    count_dates: pd.DatetimeIndex,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw = build_factor_matrix(data, factor_id).reindex_like(data.close)
    policy = factor_policy(factor_id)
    finite = np.isfinite(raw)
    positive_only = policy["eligibility_rule"] == "finite_strictly_positive"
    eligible = finite & (raw > 0) if positive_only else finite
    processed = (
        preprocess(raw, config, data.industry, data.valuation.get("total_mv"), eligible=eligible if positive_only else None)
        if eligible.loc[count_dates].any().any() else raw.where(eligible)
    )
    observed = raw.loc[count_dates]
    observed_finite = finite.loc[count_dates]
    observed_eligible = eligible.loc[count_dates]
    processed_eligible = np.isfinite(processed.loc[count_dates])
    return processed, {
        "factor_id": factor_id,
        **policy,
        "count_unit": "date_security_observations",
        "start_date": str(count_dates[0].date()),
        "end_date": str(count_dates[-1].date()),
        "counts": {
            "observations": int(observed.size),
            "raw_eligible": int(observed_eligible.to_numpy().sum()),
            "excluded_nonpositive": int((observed_finite & (observed <= 0)).to_numpy().sum()) if positive_only else 0,
            "excluded_nonfinite": int((observed.notna() & ~observed_finite).to_numpy().sum()),
            "missing": int(observed.isna().to_numpy().sum()),
            "postprocess_eligible": int(processed_eligible.to_numpy().sum()),
            "imputed": int((~observed_finite & processed_eligible).to_numpy().sum()),
        },
    }


# ---------------------------------------------------------------- 因子检验

def _correlation(factor: pd.Series, returns: pd.Series, method: str) -> float | None:
    pairs = pd.concat([factor, returns], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(pairs) < 5 or (pairs.nunique() < 2).any():
        return None
    value = float(pairs.corr(method=method).iloc[0, 1])
    return value if math.isfinite(value) else None


def _rank_ic(factor: pd.Series, returns: pd.Series) -> float | None:
    return _correlation(factor, returns, "spearman")


def _factor_buckets(cross: pd.Series, groups: int) -> pd.Series | None:
    if len(cross) < groups * 5 or cross.nunique() < groups:
        return None
    buckets = pd.qcut(cross, groups, labels=False, duplicates="drop")
    return buckets if buckets.nunique() == groups else None


def _ic_summary(values: pd.Series) -> tuple[float | None, float | None, float | None]:
    values = values.dropna()
    if values.empty:
        return None, None, None
    mean = float(values.mean())
    deviation = float(values.std()) if len(values) > 1 else None
    ratio = mean / deviation * math.sqrt(PERIODS_PER_YEAR) if deviation and deviation > 1e-12 else None
    return round(mean, 4), round(deviation, 4) if deviation is not None else None, round(ratio, 3) if ratio is not None else None


def analyze_factor(
    data: MarketData,
    factor_id: str,
    config: PreprocessConfig | None = None,
    horizon: int = 20,
    decay_lags: int = 20,
    groups: int = 5,
) -> dict[str, Any]:
    """滞后一交易日执行的因子检验及理论每日多空组合。"""
    config = config or PreprocessConfig()
    if factor_id not in FACTOR_BY_ID:
        return _research_error("unknown_factor", f"未知因子: {factor_id}")
    if not 1 <= horizon <= 120 or not 2 <= groups <= 10:
        return _research_error("invalid_parameters", "预测周期或分组数量不合法")
    try:
        data = replace(data, close=_validated_close(data))
    except ResearchDataError as error:
        return _research_error(error.code, str(error), error.status)
    try:
        factor, policy = _prepare_factor(data, factor_id, config, data.close.index)
    except ResearchDataError as error:
        return _research_error(error.code, str(error), error.status)
    if not policy["counts"]["raw_eligible"]:
        return {"factor_id": factor_id, "factor_policies": [policy], **_research_error("missing_factor_data", f"因子 {factor_id} 无符合金融域政策的可用数据", 404)}
    fwd = forward_return(data, horizon)
    spec = FACTOR_BY_ID[factor_id]
    signed = -factor if spec.direction == "low" else factor
    ic_rows: list[dict[str, Any]] = []
    for trading_date in signed.index:
        ic_pearson = _correlation(signed.loc[trading_date], fwd.loc[trading_date], "pearson")
        ic_rank = _rank_ic(signed.loc[trading_date], fwd.loc[trading_date])
        if ic_pearson is not None or ic_rank is not None:
            ic_rows.append({"date": str(trading_date.date()), "ic": ic_pearson, "rank_ic": ic_rank})
    if not ic_rows:
        return _research_error("insufficient_data", "有效截面或未来收益不足，至少需要5只可比较股票及足够交易日")
    ic_df = pd.DataFrame(ic_rows)
    ic_mean, ic_std, ic_ir = _ic_summary(ic_df["ic"])
    rank_ic_mean, _, rank_ic_ir = _ic_summary(ic_df["rank_ic"])
    ic_summary = {
        "ic_mean": ic_mean,
        "ic_std": ic_std,
        "ic_ir": ic_ir,
        "rank_ic_mean": rank_ic_mean,
        "rank_ic_ir": rank_ic_ir,
        "ic_positive_ratio": round(float((ic_df["ic"].dropna() > 0).mean()), 3),
        "periods": int(ic_df["ic"].notna().sum()),
    }
    decay: list[dict[str, Any]] = []
    for lag in [1, 5, 10, 20, 40, 60]:
        if lag > decay_lags:
            continue
        fwd_lag = forward_return(data, lag)
        values = [_rank_ic(signed.loc[trading_date], fwd_lag.loc[trading_date]) for trading_date in signed.index]
        values = [value for value in values if value is not None]
        decay.append({"lag": lag, "rank_ic": round(float(np.mean(values)), 4) if values else None})
    month_ends = _rebalance_dates(signed.index, "month")
    quantile_rows: list[dict[str, Any]] = []
    for trading_date in month_ends:
        cross = signed.loc[trading_date].dropna()
        buckets = _factor_buckets(cross, groups)
        if buckets is None:
            continue
        for group_number in range(groups):
            members = buckets[buckets == group_number].index
            realized = fwd.loc[trading_date, members]
            if realized.notna().all():
                quantile_rows.append({"date": str(trading_date.date()), "group": group_number, "return": float(realized.mean())})
    quantile_avg: list[dict[str, Any]] = []
    if quantile_rows:
        quantiles = pd.DataFrame(quantile_rows)
        for group_number, observations in quantiles.groupby("group"):
            quantile_avg.append({"group": int(group_number), "avg_return": round(float(observations["return"].mean()), 4)})
    long_short: list[dict[str, Any]] = []
    nav = 1.0
    for execution_position in range(1, len(signed.index) - 1):
        signal_date = signed.index[execution_position - 1]
        execution_date = signed.index[execution_position]
        valuation_date = signed.index[execution_position + 1]
        cross = signed.loc[signal_date].where(data.close.loc[execution_date].notna()).dropna()
        buckets = _factor_buckets(cross, groups)
        if buckets is None:
            continue
        top_codes = buckets[buckets == groups - 1].index
        bottom_codes = buckets[buckets == 0].index
        held_codes = top_codes.union(bottom_codes)
        observed = data.close.loc[valuation_date, held_codes]
        if observed.isna().any():
            return _research_error("missing_held_price", f"理论多空持仓在 {valuation_date.date()} 缺少价格，不能估值", 409)
        daily_returns = observed / data.close.loc[execution_date, held_codes] - 1
        daily_return = float(daily_returns.loc[top_codes].mean() - daily_returns.loc[bottom_codes].mean())
        nav *= 1 + daily_return
        if not math.isfinite(nav) or nav <= 0:
            return _research_error("invalid_portfolio_value", "理论多空组合净值非正或非有限，停止计算", 409)
        long_short.append({"date": str(valuation_date.date()), "nav": round(nav, 8)})
    return {
        **_method_metadata(),
        "factor_id": factor_id,
        "factor_policies": [policy],
        "label": spec.label,
        "group": spec.group,
        "direction": spec.direction,
        "ic": ic_summary,
        "ic_series": ic_rows[-120:],
        "decay": decay,
        "quantile_returns": quantile_avg,
        "long_short_nav": long_short,
        "method": {
            "execution": "previous_session_signal_at_current_close",
            "forward_return": "next_session_close_to_horizon_later_close",
            "long_short": "daily_rebalanced_1_long_1_short_zero_cost_theoretical",
            "ic_ir": "daily_descriptive_sqrt_244_without_overlap_correction",
            "financial_availability": "publish_date_strictly_before_signal_date",
        },
        "analysis_limits": ["多空净值为每日调仓、零成本、名义多头1与空头1的理论序列；借券可得性及成本未验证。", "多期未来收益重叠，IC年化IR仅描述性统计，未做独立性或显著性校正。"],
    }


# ---------------------------------------------------------------- 组合回测

@dataclass
class BacktestParams:
    factor_ids: list[str] = field(default_factory=lambda: ["ret_20d"])
    start_date: str = ""
    end_date: str = ""
    rebalance: str = "month"  # week / month
    top_n: int = 20
    weight: str = "equal"  # equal / mktcap / factor
    commission_rate: float = 0.0003
    slippage_rate: float = 0.001
    horizon: int = 20  # 持有期（调仓间隔内的收益累计天数）
    preprocess: PreprocessConfig = field(default_factory=PreprocessConfig)

    def __post_init__(self):
        if self.rebalance not in {"week", "month"} or self.weight not in {"equal", "mktcap", "factor"}:
            raise ValueError("不支持的调仓频率或加权方式")
        if not 1 <= self.top_n <= 300:
            raise ValueError("持股数量须为1至300")
        if any(not math.isfinite(rate) or not 0 <= rate <= 0.01 for rate in (self.commission_rate, self.slippage_rate)):
            raise ValueError("手续费率和滑点率须为0至0.01的有限值")


def _rebalance_dates(index: pd.DatetimeIndex, freq: str) -> list:
    if freq == "week":
        groups = pd.Series(1, index=index).groupby(pd.Grouper(freq="W-FRI"))
    elif freq == "month":
        groups = pd.Series(1, index=index).groupby(pd.Grouper(freq="ME"))
    else:
        raise ValueError("不支持的调仓频率")
    dates = []
    for _, observations in groups:
        if len(observations):
            dates.append(observations.index[-1])
    return dates


def run_portfolio_backtest(
    data: MarketData,
    params: BacktestParams,
) -> dict[str, Any]:
    """研究用收盘执行组合，保持份额、逐日盯市；缺价不能视作停牌零收益。"""
    try:
        data = replace(data, close=_validated_close(data))
        return _run_portfolio_backtest(data, params)
    except ResearchDataError as error:
        return _research_error(error.code, str(error), error.status)


def _net_rebalance_value(portfolio_value: float, held_values: dict[str, float], weights: pd.Series, rate: float) -> float:
    if rate == 0:
        return portfolio_value
    lower, upper = 0.0, portfolio_value
    all_codes = set(held_values) | set(weights.index)
    for _ in range(64):
        candidate = (lower + upper) / 2
        cost = rate * sum(abs(float(weights.get(code, 0)) * candidate - held_values.get(code, 0)) for code in all_codes)
        if candidate + cost > portfolio_value:
            upper = candidate
        else:
            lower = candidate
    return (lower + upper) / 2


def _run_portfolio_backtest(data: MarketData, params: BacktestParams) -> dict[str, Any]:
    close = data.close
    if not params.factor_ids or len(set(params.factor_ids)) != len(params.factor_ids):
        return _research_error("invalid_factors", "请选择至少一个不重复的因子")
    unknown = [factor_id for factor_id in params.factor_ids if factor_id not in FACTOR_BY_ID]
    if unknown:
        return _research_error("unknown_factor", f"未知因子: {', '.join(unknown)}")
    try:
        start = date.fromisoformat(params.start_date.replace("/", "-")) if params.start_date else close.index[0].date()
        end = date.fromisoformat(params.end_date.replace("/", "-")) if params.end_date else close.index[-1].date()
    except ValueError:
        return _research_error("invalid_date", "日期格式或日期值无效")
    if start > end:
        return _research_error("invalid_date_range", "开始日期不得晚于结束日期")
    dates = close.index[(close.index >= pd.Timestamp(start)) & (close.index <= pd.Timestamp(end))]
    if len(dates) < 30:
        return _research_error("insufficient_data", "有效交易日不足30日，请检查数据范围")
    mktcap = data.valuation.get("total_mv")
    if params.weight == "mktcap" and (mktcap is None or mktcap.empty):
        return _research_error("missing_market_cap", "市值加权缺少市值数据", 404)
    composites: list[pd.DataFrame] = []
    policies: list[dict[str, Any]] = []
    for factor_id in params.factor_ids:
        factor, policy = _prepare_factor(data, factor_id, params.preprocess, dates)
        policies.append(policy)
        if not policy["counts"]["raw_eligible"]:
            return {**_research_error("missing_factor_data", f"因子 {factor_id} 在请求区间无符合金融域政策的可用数据", 404), "factor_policies": policies}
        spec = FACTOR_BY_ID[factor_id]
        composites.append(-factor if spec.direction == "low" else factor)
    composite = sum(composites) / len(composites)
    rebalance_dates = set(_rebalance_dates(dates, params.rebalance)) - {dates[-1]}
    holdings: dict[str, float] = {}
    trades: list[dict[str, Any]] = []
    nav_points: list[dict[str, Any]] = []
    returns: list[float] = []
    turnovers: list[float] = []
    skipped: list[dict[str, Any]] = []
    cash = 1.0
    previous_nav = 1.0
    total_cost = 0.0
    executed_rebalances = 0
    entry_date: pd.Timestamp | None = None
    entry_nav = 1.0
    peak = 1.0
    max_drawdown = 0.0
    rate = params.commission_rate + params.slippage_rate
    for trading_date in dates:
        entered_before = entry_date is not None
        observed = close.loc[trading_date]
        missing_held = [code for code in holdings if pd.isna(observed[code])]
        if missing_held:
            return _research_error("missing_held_price", f"持仓在 {trading_date.date()} 缺价: {', '.join(sorted(missing_held))}，不能估值", 409)
        held_values = {code: quantity * float(observed[code]) for code, quantity in holdings.items()}
        portfolio_value = cash + sum(held_values.values())
        if trading_date in rebalance_dates:
            signal_position = close.index.get_loc(trading_date) - 1
            if signal_position >= 0:
                signal_date = close.index[signal_position]
                cross = composite.loc[signal_date].where(observed.notna()).dropna()
                if len(cross) < params.top_n:
                    skipped.append({"date": str(trading_date.date()), "reason": "insufficient_eligible_stocks", "eligible": len(cross)})
                else:
                    selected = cross.sort_index().sort_values(ascending=False, kind="stable").head(params.top_n)
                    if params.weight == "mktcap":
                        caps = mktcap.reindex_like(close).loc[signal_date, selected.index]
                        if not (np.isfinite(caps) & (caps > 0)).all():
                            return _research_error("missing_market_cap", f"市值加权在信号日 {signal_date.date()} 缺少有效市值", 409)
                        weights = caps / caps.sum()
                    elif params.weight == "factor":
                        ranks = selected.rank(pct=True)
                        weights = ranks / ranks.sum()
                    else:
                        weights = pd.Series(1 / len(selected), index=selected.index)
                    net_value = _net_rebalance_value(portfolio_value, held_values, weights, rate)
                    target_values = {code: float(weight) * net_value for code, weight in weights.items()}
                    traded_notional = sum(abs(target_values.get(code, 0) - held_values.get(code, 0)) for code in set(target_values) | set(held_values))
                    cost = traded_notional * rate
                    total_cost += cost
                    turnovers.append(traded_notional / portfolio_value)
                    for code in sorted(set(target_values) | set(held_values)):
                        delta = target_values.get(code, 0) - held_values.get(code, 0)
                        if abs(delta) <= 1e-12 * portfolio_value:
                            continue
                        if code not in target_values:
                            action = "卖出"
                        elif code not in held_values:
                            action = "买入"
                        else:
                            action = "加仓" if delta > 0 else "减仓"
                        factor_value = composite.loc[signal_date, code]
                        trades.append({
                            "date": str(trading_date.date()),
                            "signal_date": str(signal_date.date()),
                            "code": code,
                            "name": data.name.get(code, code),
                            "weight": round(float(weights.get(code, 0)), 8),
                            "action": action,
                            "factor_value": round(float(factor_value), 6) if pd.notna(factor_value) else None,
                            "notional": round(abs(delta), 10),
                            "cost": round(abs(delta) * rate, 10),
                        })
                    holdings = {code: value / float(observed[code]) for code, value in target_values.items()}
                    cash = portfolio_value - cost - sum(target_values.values())
                    portfolio_value = cash + sum(target_values.values())
                    executed_rebalances += 1
                    if entry_date is None:
                        entry_date, entry_nav = trading_date, portfolio_value
        if not math.isfinite(portfolio_value) or portfolio_value <= 0:
            return _research_error("invalid_portfolio_value", "组合净值非正或非有限，停止计算", 409)
        peak = max(peak, portfolio_value)
        max_drawdown = max(max_drawdown, (peak - portfolio_value) / peak)
        if entered_before:
            returns.append(portfolio_value / previous_nav - 1)
            nav_points.append({"date": str(trading_date.date()), "nav": round(portfolio_value, 8)})
            previous_nav = portfolio_value
    if not nav_points:
        return _research_error("insufficient_data", "回测未产生收益序列，请检查所选因子及持股数量")
    from app.services.backtest.metrics import sharpe_ratio, sortino_ratio

    final_value = previous_nav
    annualized_return = math.expm1(math.log(final_value) * PERIODS_PER_YEAR / len(returns))
    metrics = {
        "total_return_pct": round((final_value - 1) * 100, 4),
        "annualized_return_pct": round(annualized_return * 100, 4),
        "annualized_vol_pct": round(float(np.std(returns, ddof=1) * math.sqrt(PERIODS_PER_YEAR) * 100), 4) if len(returns) > 1 else 0.0,
        "sharpe": round(sharpe_ratio(returns, PERIODS_PER_YEAR), 4),
        "sortino": round(sortino_ratio(returns, PERIODS_PER_YEAR), 4),
        "calmar": round(annualized_return / max_drawdown, 4) if max_drawdown > 1e-12 else None,
        "max_drawdown_pct": round(max_drawdown * 100, 4),
        "win_rate": round(sum(value > 0 for value in returns) / len(returns) * 100, 2),
        "turnover_rate": round(float(np.mean(turnovers)), 4) if turnovers else 0.0,
        "periods": len(returns),
    }
    return {
        **_method_metadata(),
        "metrics": metrics,
        "factor_policies": policies,
        "nav": nav_points,
        "trades": trades[-500:],
        "trades_total": len(trades),
        "trades_truncated": len(trades) > 500,
        "rebalance_count": executed_rebalances,
        "skipped_rebalances": skipped,
        "entry_date": str(entry_date.date()),
        "entry_nav": round(entry_nav, 10),
        "total_cost": round(total_cost, 10),
        "method": {
            "execution": "previous_session_signal_at_current_close",
            "valuation": "fixed_units_marked_to_observed_close",
            "cost": "self_financed_absolute_traded_notional_times_commission_plus_slippage",
            "annualized_return": "geometric_244_observed_sessions",
            "factor_weight": "selected_factor_rank_weight",
            "first_return": "includes_entry_cost_before_first_mark",
            "financial_availability": "publish_date_strictly_before_signal_date",
        },
        "params": {
            "factor_ids": params.factor_ids,
            "rebalance": params.rebalance,
            "top_n": params.top_n,
            "weight": params.weight,
            "commission_rate": params.commission_rate,
            "slippage_rate": params.slippage_rate,
            "method_version": METHOD_VERSION,
        },
    }
