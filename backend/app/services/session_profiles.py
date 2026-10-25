"""国内期货品种交易时段配置（北京时间）

按常见规则分组：日盘节段 + 夜盘收盘时刻。
夜盘只存在于周一~周五晚间：周五夜盘可跨至周六凌晨；周六白天/晚间、
周日全天、周一凌晨均无交易（周日晚没有夜盘）。
不含法定节假日（后续可接日历表）。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

# 时段：start/end 为 time；若 end < start 表示跨午夜
Session = tuple[time, time]

# 日盘：商品（含 10:15-10:30 休市）
DAY_COMMODITY: list[Session] = [
    (time(9, 0), time(10, 15)),
    (time(10, 30), time(11, 30)),
    (time(13, 30), time(15, 0)),
]

# 日盘：股指
DAY_INDEX: list[Session] = [
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 0)),
]

# 日盘：国债
DAY_BOND: list[Session] = [
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 15)),
]

# 夜盘节段（单独，可与日盘叠加）
NIGHT_2300: list[Session] = [(time(21, 0), time(23, 0))]
NIGHT_0100: list[Session] = [(time(21, 0), time(1, 0))]  # 跨午夜
NIGHT_0230: list[Session] = [(time(21, 0), time(2, 30))]

# 配置项：day_sessions + night_sessions + label
Profile = dict[str, Any]

PROFILES: dict[str, Profile] = {
    "commodity_day": {
        "label": "商品日盘",
        "day": DAY_COMMODITY,
        "night": [],
    },
    "commodity_n2300": {
        "label": "商品+夜盘至23:00",
        "day": DAY_COMMODITY,
        "night": NIGHT_2300,
    },
    "commodity_n0100": {
        "label": "商品+夜盘至01:00",
        "day": DAY_COMMODITY,
        "night": NIGHT_0100,
    },
    "commodity_n0230": {
        "label": "商品+夜盘至02:30",
        "day": DAY_COMMODITY,
        "night": NIGHT_0230,
    },
    "index": {
        "label": "股指期货",
        "day": DAY_INDEX,
        "night": [],
    },
    "bond": {
        "label": "国债期货",
        "day": DAY_BOND,
        "night": [],
    },
}

# 品种代码 → profile key
# 夜盘收盘以国内主流交易所规则为准（北京时间）：
# - 02:30：贵金属/原油
# - 01:00：有色
# - 23:00：黑色、橡胶、能源化工、多数农产品
# - 无夜盘：股指/国债/部分农产品与新品种
PRODUCT_PROFILE: dict[str, str] = {
    # 夜盘 02:30 —— 贵金属、原油
    "AU": "commodity_n0230",
    "AG": "commodity_n0230",
    "SC": "commodity_n0230",
    # 夜盘 01:00 —— 有色
    "CU": "commodity_n0100",
    "AL": "commodity_n0100",
    "ZN": "commodity_n0100",
    "PB": "commodity_n0100",
    "NI": "commodity_n0100",
    "SN": "commodity_n0100",
    "SS": "commodity_n0100",
    "BC": "commodity_n0100",
    "AO": "commodity_n0100",
    # 夜盘 23:00 —— 黑色系（此前误配 02:30，会导致 23:00 后仍显示运行中）
    "RB": "commodity_n2300",
    "HC": "commodity_n2300",
    "WR": "commodity_n2300",
    "I": "commodity_n2300",
    "J": "commodity_n2300",
    "JM": "commodity_n2300",
    "SF": "commodity_n2300",
    "SM": "commodity_n2300",
    # 夜盘 23:00 —— 橡胶/纸浆/沥青/燃油
    "RU": "commodity_n2300",
    "NR": "commodity_n2300",
    "SP": "commodity_n2300",
    "BU": "commodity_n2300",
    "FU": "commodity_n2300",
    "LU": "commodity_n2300",
    # 夜盘 23:00 —— 农产品/化工等
    "M": "commodity_n2300",
    "Y": "commodity_n2300",
    "P": "commodity_n2300",
    "A": "commodity_n2300",
    "B": "commodity_n2300",
    "C": "commodity_n2300",
    "CS": "commodity_n2300",
    "RR": "commodity_n2300",
    "RM": "commodity_n2300",
    "OI": "commodity_n2300",
    "CF": "commodity_n2300",
    "CY": "commodity_n2300",
    "SR": "commodity_n2300",
    "TA": "commodity_n2300",
    "MA": "commodity_n2300",
    "FG": "commodity_n2300",
    "SA": "commodity_n2300",
    "PF": "commodity_n2300",
    "L": "commodity_n2300",
    "V": "commodity_n2300",
    "PP": "commodity_n2300",
    "EG": "commodity_n2300",
    "EB": "commodity_n2300",
    "PG": "commodity_n2300",
    "PX": "commodity_n2300",
    "SH": "commodity_n2300",
    "BR": "commodity_n2300",
    # 仅日盘
    "IF": "index",
    "IC": "index",
    "IM": "index",
    "IH": "index",
    "T": "bond",
    "TF": "bond",
    "TS": "bond",
    "TL": "bond",
    "AP": "commodity_day",
    "CJ": "commodity_day",
    "JD": "commodity_day",
    "LH": "commodity_day",
    "SI": "commodity_day",
    "LC": "commodity_day",
    "UR": "commodity_day",
    "WH": "commodity_day",
    "PM": "commodity_day",
    "RI": "commodity_day",
    "RS": "commodity_day",
    "FB": "commodity_day",
    "BB": "commodity_day",
    "EC": "commodity_day",
}

DEFAULT_PROFILE_KEY = "commodity_n2300"


def get_profile_key(product_code: str) -> str:
    """品种代码 → 时段配置 key"""
    return PRODUCT_PROFILE.get(product_code.upper(), DEFAULT_PROFILE_KEY)


def get_profile(product_code: str) -> Profile:
    """获取品种时段配置"""
    key = get_profile_key(product_code)
    return PROFILES[key]


def all_sessions(profile: Profile) -> list[Session]:
    """日盘 + 夜盘合并"""
    day: list[Session] = list(profile.get("day") or [])
    night: list[Session] = list(profile.get("night") or [])
    return day + night


def _roll_to_weekday(d: date, *, forward: bool) -> date:
    """向前/向后跳过周末到最近的工作日。"""
    while d.weekday() >= 5:  # 5=周六 6=周日
        d = d + timedelta(days=1) if forward else d - timedelta(days=1)
    return d


def _active_session_block(
    profile: Profile,
    at: datetime,
) -> list[tuple[datetime, datetime]] | None:
    """返回 at 所属交易日的连续交易分钟轴（休盘间隔不计分钟）。

    有夜盘的品种从前一自然日夜盘连续到次日日盘。多数夜盘长度恰好可被
    30/60 整除，因此旧的“夜盘、日盘分别重置”问题被掩盖；02:30 收盘的
    品种夜盘共 330 分钟，其 60m 余下 30 分钟必须延续到日盘 09:30。

    夜盘只存在于周一~周五晚间：night_date 向前回退到最近的工作日傍晚
    （周一凌晨→周五、周日凌晨→周五），day_date 为其次个工作日——
    绝不构造「周日晚」的幻影交易轴（2026-08-23 周日幽灵 K 根因之一：
    旧逻辑周一凌晨/周日晚都会拼出周日 21:00-23:00 的轴，period_end
    随之错乱，future 过滤放行周日幽灵 bar、误杀正常 bar）。
    """

    def on_start_date(session: Session, start_date) -> tuple[datetime, datetime]:
        start, end = session
        tz = at.tzinfo
        start_dt = datetime.combine(start_date, start, tzinfo=tz)
        end_date = start_date + timedelta(days=1) if end < start else start_date
        return start_dt, datetime.combine(end_date, end, tzinfo=tz)

    day_sessions: list[Session] = list(profile.get("day") or [])
    night_sessions: list[Session] = list(profile.get("night") or [])

    if night_sessions:
        first_night_start = min(start for start, _ in night_sessions)
        night_date = (
            at.date()
            if at.time() >= first_night_start
            else at.date() - timedelta(days=1)
        )
        night_date = _roll_to_weekday(night_date, forward=False)
        day_date = _roll_to_weekday(night_date + timedelta(days=1), forward=True)
        concrete = [
            *(on_start_date(session, night_date) for session in night_sessions),
            *(on_start_date(session, day_date) for session in day_sessions),
        ]
    else:
        day_date = _roll_to_weekday(at.date(), forward=False)
        concrete = [on_start_date(session, day_date) for session in day_sessions]

    concrete.sort(key=lambda item: item[0])
    if concrete and concrete[0][0] <= at <= concrete[-1][1]:
        return concrete
    return None


def _axis_datetime(
    block: list[tuple[datetime, datetime]],
    position: int,
    *,
    prefer_next_at_break: bool = False,
) -> datetime:
    """把累计交易分钟位置映射回实际时钟。"""
    remaining = max(position, 0)
    for index, (start, end) in enumerate(block):
        duration = int((end - start).total_seconds() // 60)
        if remaining < duration:
            return start + timedelta(minutes=remaining)
        if remaining == duration:
            if prefer_next_at_break and index + 1 < len(block):
                return block[index + 1][0]
            return end
        remaining -= duration
    return block[-1][1]


def _elapsed_trading_minutes(
    block: list[tuple[datetime, datetime]],
    at: datetime,
) -> tuple[int, datetime | None, datetime | None]:
    """返回累计分钟、当前节段末端、最近已完成节段末端。"""
    elapsed = 0
    active_end: datetime | None = None
    previous_end: datetime | None = None
    for start, end in block:
        duration = int((end - start).total_seconds() // 60)
        if at < start:
            break
        if start <= at <= end:
            elapsed += int((at - start).total_seconds() // 60)
            active_end = end
            break
        elapsed += duration
        previous_end = end
    return elapsed, active_end, previous_end


def period_end_for_product(
    product_code: str,
    at: datetime,
    minutes: int,
) -> datetime | None:
    """按品种实际交易分钟轴计算当前多分钟 K 线的结束时刻。

    日盘的多个节段视为连续交易分钟轴，10:15-10:30 和午休不计分钟；
    夜盘与其后日盘共用交易分钟轴；末尾不足完整周期时钳制到闭市时刻。
    """
    if minutes <= 0:
        return None
    block = _active_session_block(get_profile(product_code), at)
    if not block:
        return None

    total_minutes = sum(
        int((end - start).total_seconds() // 60) for start, end in block
    )
    elapsed, active_end, previous_end = _elapsed_trading_minutes(block, at)

    if active_end is not None and at == active_end and elapsed % minutes == 0:
        return active_end
    if active_end is None and elapsed % minutes == 0:
        # 休盘前恰好封桶：短周期停留在上一根，不提前产生复市后的 forming 标签。
        return previous_end
    target = (elapsed // minutes + 1) * minutes
    target = min(target, total_minutes)
    return _axis_datetime(block, target)


# 收盘打印宽限：交易所收盘整点（23:00/10:15/11:30/15:00 等）后 1 分钟内
# 到达的 tick 视为该时段的收盘打印（左闭右开），归入收盘桶而非下一时段。
_CLOSE_PRINT_GRACE = timedelta(seconds=60)


def _clamp_close_print(
    block: list[tuple[datetime, datetime]],
    at: datetime,
) -> datetime:
    """收盘打印宽限内的时刻回退到时段末内侧，供桶起点归属判断。"""
    for _start, end in block:
        if end <= at < end + _CLOSE_PRINT_GRACE:
            return end - timedelta(microseconds=1)
    return at


def fold_tick_minute_edge(product_code: str, at: datetime) -> datetime:
    """实时 tick 分桶口径：整分钟 00 秒的 tick 折回上一分钟内侧。

    交易所秒级快照里时间戳为 XX:00:00 的是上一分钟的收盘打印
    （深历史 pg-tickdata 中该笔位于下一行 eob=X+1m、读取时折叠进
    上一桶，见 tickdata_kline._rows_to_bars），实时 forming 分桶必须
    同口径，否则每根分钟 bar 的最后一笔被错开到下一根：上一桶
    close/volume 少最后一笔、下一桶 open 沾上收盘打印价，且与
    VVTR 深历史回补值交接时整根跳变。

    仅用于「tick 成交时刻 → 所属桶」的实时聚合入口；eob→bob 等
    「桶端点换算」路径传入的整分是桶自然起点，绝不能折。

    时段开盘整点（09:00:00/21:00:00/10:30 复市等，前一瞬间不在
    交易块内）不折，归开盘新桶；收盘整点（10:15/11:30/15:00/
    23:00/02:30）折入收盘桶，与 _clamp_close_print 语义一致。
    品种无 profile 或时刻在轴外时原样返回。
    """
    if at.second != 0 or at.microsecond != 0:
        return at
    block = _active_session_block(get_profile(product_code), at)
    if not block:
        return at
    prev = at - timedelta(microseconds=1)
    for start, end in block:
        if start <= prev <= end:
            return prev
    return at


def period_start_for_product(
    product_code: str,
    at: datetime,
    minutes: int,
) -> datetime | None:
    """返回 at 所属多分钟桶在交易分钟轴上的实际起点。

    边界语义（左闭右开，与主流行情软件/vnpy 社区实践一致）：
    - 开盘边界（09:00:00 整、盘前集合竞价 tick）→ 归入开盘新桶；
    - 收盘边界（23:00:00/10:15:00/15:00:00 整及其后 1 分钟内的收盘
      打印）→ 归入收盘桶。修复前 23:00 整的收盘打印被 prefer_next_at_break
      映射成「次日 09:00」幽灵 forming bar，跨夜存活后与开盘首 tick 同键
      合并，导致新 K 开盘价被粘成昨夜收盘打印价、真实末根收线固化丢失
      （2026-08-25 实测）。
    """
    if minutes <= 0:
        return None
    block = _active_session_block(get_profile(product_code), at)
    if not block:
        return None
    effective = _clamp_close_print(block, at)
    elapsed, _, _ = _elapsed_trading_minutes(block, effective)
    start_position = (elapsed // minutes) * minutes
    return _axis_datetime(block, start_position, prefer_next_at_break=True)
