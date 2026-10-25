"""天勤 TqSdk 历史分钟线获取层 —— 从 TqSdk 免费版拉取并标准化

仿 akshare_history.py 模式：延迟导入、同步 def，调用方用 asyncio.to_thread 包装。
TqApi 跑自己的事件循环且 wait_update 阻塞，不能在 FastAPI async 里直接用，
必须放独立线程（to_thread），每次调用后 close。

合约映射：品种 code rb → tqsdk 连续主力 KQ.m@SHFE.rb（自动跟踪主力换月）。
免费版 get_kline_serial 最多 8964 根：1m 约 2-3 周，15m 约 6 个月。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("qihuo.tqsdk")

# tqsdk 的 datetime 列是交易所本地时间（北京）的绝对时刻，
# 转换必须用北京时区，否则 bar 时间会偏移 8 小时（显示成 UTC 墙钟）。
_BJ_TZ = timezone(timedelta(hours=8))

# 交易所中文名 → tqsdk 前缀
EXCHANGE_PREFIX: dict[str, str] = {
    "上期所": "SHFE",
    "大商所": "DCE",
    "郑商所": "CZCE",
    "中金所": "CFFEX",
    "广期所": "GFEX",
    "能源中心": "INE",
}

# tqsdk 周期（秒）：1m=60, 5m=300, 15m=900, 30m=1800, 60m=3600, 1d=86400
PERIOD_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "60m": 3600,
    "1d": 86400,
}

# 免费版单次最大根数
MAX_DATA_LENGTH = 8964


# 上期所代码里实际属于能源中心（INE）的品种（contracts.py 数据标为上期所）
_INE_CODES = {"nr", "sc", "lu", "bc"}


def _build_tqsdk_symbol(code: str, exchange_cn: str) -> str:
    """品种 code + 交易所中文名 → tqsdk 连续主力符号 KQ.m@SHFE.rb

    天勤代码规范：郑商所品种代码大写（KQ.m@CZCE.TA）；
    nr/sc/lu/bc 属能源中心（INE），contracts 数据标为上期所，此处修正。
    """
    prefix = EXCHANGE_PREFIX.get(exchange_cn)
    if not prefix:
        return ""
    sym_code = code.lower()
    if prefix == "CZCE":
        # 郑商所天勤代码大写（TA/MA/CF...）
        sym_code = sym_code.upper()
    elif prefix == "SHFE" and sym_code in _INE_CODES:
        # 20号胶/原油/低硫燃料油/国际铜 → 能源中心
        prefix = "INE"
    return f"KQ.m@{prefix}.{sym_code}"


def fetch_minute_history(
    code: str,
    exchange_cn: str,
    period: str = "1m",
    data_length: int = MAX_DATA_LENGTH,
    user: str = "",
    password: str = "",
) -> list[dict[str, Any]]:
    """从 TqSdk 拉取历史分钟 K 线（同步调用）

    Args:
        code: 品种代码，如 "rb"
        exchange_cn: 交易所中文名，如 "上期所"
        period: 周期 "1m"/"5m"/"15m"/"30m"/"60m"/"1d"（1d 免费版 8964 根≈35 年）
        data_length: 拉取根数，免费版上限 8964
        user/password: 快期账户凭据（从 .env 注入）

    Returns:
        标准化 bar 列表 [{symbol,time,open,high,low,close,volume,hold}]，按时间升序。
        失败返回空列表（调用方降级到新浪）。
    """
    symbol = _build_tqsdk_symbol(code, exchange_cn)
    if not symbol:
        logger.warning("无法映射品种 %s（%s）到 tqsdk 符号", code, exchange_cn)
        return []

    seconds = PERIOD_SECONDS.get(period)
    if seconds is None:
        logger.warning("不支持的周期: %s", period)
        return []

    try:
        # 延迟导入，避免未装 tqsdk 时启动失败
        from tqsdk import TqApi, TqAuth
    except ImportError:
        logger.warning("tqsdk 未安装，跳过历史分钟线拉取")
        return []

    api = None
    try:
        if user and password:
            api = TqApi(auth=TqAuth(user, password))
        else:
            # 未配置凭据时尝试匿名（部分行情可用，历史数据大概率需要登录）
            api = TqApi()

        klines = api.get_kline_serial(
            symbol, seconds, data_length=min(data_length, MAX_DATA_LENGTH)
        )
        if klines is None or len(klines) == 0:
            logger.info("tqsdk %s %s 返回空", symbol, period)
            return []

        bars = _dataframe_to_bars(klines, symbol, period)
        logger.info("tqsdk 拉取 %s %s: %d 根", symbol, period, len(bars))
        return bars
    except Exception as exc:
        # 部分网络（部分企业内网出口）对 auth.shinnytech.com 的 TLS 间歇性切断，
        # 短暂等待后重试一次，显著提升成功率（失败率 ~55% → ~25%）。
        logger.warning(
            "tqsdk 拉取 %s %s 失败（准备重试）: %s", symbol, period, exc
        )
        try:
            if api is not None:
                api.close()
        except Exception:
            pass
        api = None
        try:
            time.sleep(1.0)
            if user and password:
                api = TqApi(auth=TqAuth(user, password))
            else:
                api = TqApi()
            klines = api.get_kline_serial(
                symbol, seconds, data_length=min(data_length, MAX_DATA_LENGTH)
            )
            if klines is None or len(klines) == 0:
                return []
            bars = _dataframe_to_bars(klines, symbol, period)
            logger.info(
                "tqsdk 重试拉取 %s %s: %d 根", symbol, period, len(bars)
            )
            return bars
        except Exception as exc2:
            logger.warning(
                "tqsdk 重试拉取 %s %s 仍失败（降级新浪）: %s",
                symbol,
                period,
                exc2,
            )
            return []
    finally:
        if api is not None:
            try:
                api.close()
            except Exception:
                pass


def _dataframe_to_bars(
    df: Any,
    tqsdk_symbol: str,
    period: str = "1m",
) -> list[dict[str, Any]]:
    """tqsdk DataFrame → 标准 bar dict 列表

    tqsdk datetime 是纳秒时间戳（epoch nanoseconds），过滤掉 0（无效行）。
    symbol 用 tqsdk 连续主力的底层具体合约（df.instrument_id）。
    datetime 是北京时间（交易所本地时间），用北京时区转换；
    日线统一为 YYYY-MM-DD（与新浪/项目约定一致）。
    """
    bars: list[dict[str, Any]] = []
    # instrument_id 是当前主力具体合约（如 rb2610）
    instrument = getattr(df, "instrument_id", "") or tqsdk_symbol
    for _, row in df.iterrows():
        try:
            ts_ns = int(row["datetime"])
            if ts_ns <= 0:
                continue  # tqsdk 用 0 填充未来行
            dt = datetime.fromtimestamp(ts_ns / 1e9, tz=_BJ_TZ)
            o = float(row["open"])
            h = float(row["high"])
            l = float(row["low"])
            c = float(row["close"])
            v = int(float(row.get("volume", 0) or 0))
            hold = int(float(row.get("close_oi", row.get("open_oi", 0)) or 0))
            if not (o > 0 and h > 0 and l > 0 and c > 0):
                continue
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            if period == "1d":
                time_str = time_str[:10]
            bars.append(
                {
                    "symbol": str(instrument),
                    "time": time_str,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": v,
                    "hold": hold,
                }
            )
        except (ValueError, TypeError, KeyError):
            continue
    # 按时间升序
    bars.sort(key=lambda b: b["time"])
    return bars
