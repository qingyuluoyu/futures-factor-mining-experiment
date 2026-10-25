"""日志配置模块 —— 控制台 + 文件输出到 logs/"""

import logging
import os
from datetime import datetime
from pathlib import Path


def setup_logger(log_level: str = "INFO") -> logging.Logger:
    """配置并返回应用日志器"""
    logger = logging.getLogger("qihuo")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件输出到 logs/ 目录
    log_dir = Path(__file__).resolve().parents[3] / "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file = log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
