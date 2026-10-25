import os
from pathlib import Path

from scripts.local_service import configure_environment


def verify_environment(settings):
    if settings.redis_host != "127.0.0.1" or settings.redis_port != 6380:
        raise RuntimeError("Dedicated local Redis 127.0.0.1:6380 required")
    if os.environ.get("SINA_REPLICATE_REDIS_URL", "").strip():
        raise RuntimeError("Remote replication is not allowed in the local preview connector")
    if os.environ.get("SINA_BRIDGE_PORT", "8110") != "8110":
        raise RuntimeError("Local preview connector requires port 8110")


def run_connector():
    configure_environment(Path(__file__).resolve().parents[2])
    from app.core.config import load_settings
    import uvicorn

    verify_environment(load_settings())
    uvicorn.run("app.connectors.sina.api:create_app", factory=True, host="127.0.0.1", port=8110, access_log=False)


if __name__ == "__main__":
    run_connector()
