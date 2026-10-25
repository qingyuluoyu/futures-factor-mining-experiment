"""sina-bridge 配置（全部非秘密，可版本化/环境变量覆盖）"""

from __future__ import annotations

import os

# HTTP 服务监听端口（仅 compose 内网，不绑宿主端口）
BRIDGE_PORT = int(os.environ.get("SINA_BRIDGE_PORT", "8110"))

# quote 轮询间隔（秒）
# 交易时段（sina 为行情主源/兜底源，秒级）
POLL_INTERVAL_TRADING = float(os.environ.get("SINA_POLL_INTERVAL_TRADING", "3"))
# 休市（保持缓存存活）
POLL_INTERVAL_CLOSED = float(os.environ.get("SINA_POLL_INTERVAL_CLOSED", "60"))
# 推送源（vvtr WS 桥）供数时：sina 只补昨结/名称等日内静态字段，降频
POLL_INTERVAL_PUSH_ACTIVE = float(
    os.environ.get("SINA_POLL_INTERVAL_PUSH_ACTIVE", "60")
)

# 月度全合约循环（原 scheduler._background_monthly_fetch_loop 节奏）
MONTHLY_INTERVAL = float(os.environ.get("SINA_MONTHLY_INTERVAL", "30"))
MONTHLY_BATCH_SIZE = int(os.environ.get("SINA_MONTHLY_BATCH_SIZE", "30"))
MONTHLY_BATCH_DELAY = float(os.environ.get("SINA_MONTHLY_BATCH_DELAY", "0.1"))

# 心跳键与间隔
HEARTBEAT_KEY = "market:live:sina:bridge:heartbeat"
HEARTBEAT_INTERVAL = 30.0
HEARTBEAT_TTL = 120.0

# 源键 TTL（与 vvtr 源键一致）
SOURCE_KEY_TTL = 7 * 24 * 3600

# 推流副本（可选）：58 桥中心化部署时，把 quote 源键/月度键/心跳
# 双写到其他部署的 Redis（例 redis://192.0.2.10:6380/0），空=不推流
REPLICATE_REDIS_URL = os.environ.get("SINA_REPLICATE_REDIS_URL", "").strip()


def bridge_base_url() -> str:
    """backend/脚本侧访问桥的基址（compose 网络服务名）"""
    return (
        os.environ.get("SINA_BRIDGE_BASE_URL", "").strip()
        or "http://sina-bridge:8110"
    ).rstrip("/")
