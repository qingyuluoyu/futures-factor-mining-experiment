"""sina-bridge 容器入口：python -m app.connectors.sina.main"""

from __future__ import annotations

import logging
import os

from .api import create_app
from .config import BRIDGE_PORT


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    import uvicorn

    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=BRIDGE_PORT,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
