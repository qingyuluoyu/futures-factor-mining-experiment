"""应用配置模块 —— 使用 pydantic-settings 从 .env 读取配置"""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings

# .env 位于项目根目录（backend/ 的上一级）
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """应用配置，从 .env 文件读取"""

    # PostgreSQL
    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str
    postgres_port: int

    # TickData 历史 K线库（pg-tickdata：8亿级全周期历史 K线，2005 起；定期增量灌入）
    # 仅用于历史回溯（K线图懒加载拖动），未配置时自动降级到 HTTP/PG。
    # 密码勿入库/镜像，由各环境 .env 注入。
    tickdata_postgres_host: str = ""
    tickdata_postgres_port: int = 5434
    tickdata_postgres_user: str = ""
    tickdata_postgres_password: str = ""
    tickdata_postgres_db: str = "tickdata"

    # Redis
    redis_host: str
    redis_port: int
    redis_db: int = 0

    # Qdrant
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333

    # 应用
    app_env: str = "development"
    log_level: str = "INFO"
    registration_policy: Literal["open", "managed"] = "open"

    # 实时行情渠道冷启动兜底（运行时以 DB/Redis 为准）
    market_data_channel_default: str = "sina"

    # （SimNow CTP 配置已移除——渠道 2026-08-24 下线）

    # openctp 模拟（公众号注册；密码勿入库）
    openctp_broker_id: str = "9999"
    openctp_investor_id: str = "19659"
    openctp_password: str = ""
    openctp_724_investor: str = "19659"
    openctp_sim_investor: str = "18962"
    openctp_md_front_724: str = "tcp://trading.openctp.cn:30011"
    openctp_md_front_sim: str = "tcp://trading.openctp.cn:30011"
    openctp_app_id: str = ""
    openctp_auth_code: str = ""
    openctp_user_product_info: str = "qihuo_oc"

    # 天勤 TqSdk（免费版需快期账户；密码勿入库，由 .env 注入）
    tqsdk_user: str = ""
    tqsdk_password: str = ""

    # 真实期货公司 CTP 行情（Level-1 一档免费；密码勿入库，由 .env 注入）
    real_ctp_broker_id: str = ""
    real_ctp_investor_id: str = ""
    real_ctp_password: str = ""
    real_ctp_md_front: str = ""
    real_ctp_app_id: str = ""
    real_ctp_auth_code: str = ""
    real_ctp_user_product_info: str = "qihuo_real"

    # CTP 行情前置（ctp_sim 渠道：用户在管理端自行填写，此处默认空）
    ctp_sim_md_front: str = ""
    ctp_sim_broker_id: str = "9999"
    ctp_sim_investor_id: str = "qihuo_sim"
    ctp_sim_password: str = ""
    ctp_sim_user_product_info: str = "qihuo_sim"

    # K 线远端兜底源（如 http://192.0.2.10:8002）：
    # 本地所有源不可用（网络/封禁）时，从该服务器 /api/market/kline 中转历史
    kline_remote_base: str = ""

    # A股数据（DaoShare Tushare 代理；.env 的 TUSHARE_TOKEN）
    tushare_token: str = ""
    stock_replay_enabled: bool = False
    stock_research_jobs_enabled: bool = False
    stock_legacy_sync_enabled: bool = True
    gp_jobs_enabled: bool = False
    stock_eod_enabled: bool = False
    stock_eod_sync_enabled: bool = False
    stock_eod_catchup_enabled: bool = False
    stock_daily_archive_enabled: bool = False

    # JWT 认证
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    # 登录会话默认 7 天（access 与 refresh 对齐，避免频繁掉线）
    jwt_access_token_expire_minutes: int = 10080
    jwt_refresh_token_expire_days: int = 7

    @property
    def database_url(self) -> str:
        """异步数据库连接 URL"""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def tickdata_database_url(self) -> str:
        """TickData 历史 K线库异步连接 URL（pg-tickdata）"""
        return (
            f"postgresql+asyncpg://{self.tickdata_postgres_user}:{self.tickdata_postgres_password}"
            f"@{self.tickdata_postgres_host}:{self.tickdata_postgres_port}"
            f"/{self.tickdata_postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """Redis 连接 URL"""
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    model_config = {
        "env_file": str(_ENV_FILE),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


def load_settings() -> Settings:
    """加载应用配置"""
    return Settings()
