"""品种发现与分批 —— 主力合约列表切成 5–10 个/批"""

from __future__ import annotations

from typing import Any

from app.services.ai.research.types import ProductRef
from app.services.contract_master import get_master_contracts


# 每批品种数约束
_MIN_BATCH = 5
_MAX_BATCH = 10
_DEFAULT_BATCH = 8
_DEFAULT_MAX_PRODUCTS = 120


def clamp_batch_size(raw: int | None) -> int:
    """将 batch_size 限制在 5–10，非法则用默认 8。"""
    if raw is None:
        return _DEFAULT_BATCH
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_BATCH
    if value < _MIN_BATCH:
        return _MIN_BATCH
    if value > _MAX_BATCH:
        return _MAX_BATCH
    return value


def clamp_max_products(raw: int | None) -> int:
    """安全上限，防止异常膨胀。"""
    if raw is None:
        return _DEFAULT_MAX_PRODUCTS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_PRODUCTS
    if value < 1:
        return 1
    if value > 300:
        return 300
    return value


def chunk_list(items: list[ProductRef], size: int) -> list[list[ProductRef]]:
    """按固定大小切片（纯函数）。"""
    if size <= 0:
        return [items] if items else []
    batches: list[list[ProductRef]] = []
    for index in range(0, len(items), size):
        batches.append(items[index : index + size])
    return batches


def make_agent_name(index: int, batch: list[ProductRef]) -> str:
    """子代理显示名：子代理-N · RB/HC/I…"""
    codes = [item["product_code"] for item in batch[:4]]
    suffix = "/".join(codes)
    if len(batch) > 4:
        suffix = f"{suffix}…"
    return f"子代理-{index} · {suffix}"


async def list_master_products(
    redis_conn: Any,
    scope: str | list[str],
    max_products: int,
) -> tuple[list[ProductRef], str | None]:
    """从 Redis 主力缓存列出待调研品种。

    scope:
      - \"all\"：全部主力
      - 列表：品种代码（RB）或合约（rb2610）过滤
    返回 (products, error_message)
    """
    if redis_conn is None:
        return [], "Redis 不可用，无法读取主力合约"

    masters = await get_master_contracts(redis_conn)
    if not masters:
        return [], "主力合约缓存为空，请稍后重试"

    # 规范化 scope
    filter_codes: set[str] | None = None
    if isinstance(scope, list):
        filter_codes = set()
        for item in scope:
            token = str(item).strip()
            if not token:
                continue
            # 合约 → 品种代码
            code = "".join(ch for ch in token if ch.isalpha()).upper()
            if code:
                filter_codes.add(code)
        if not filter_codes:
            filter_codes = None
    elif isinstance(scope, str) and scope.strip().lower() not in ("", "all", "*"):
        code = "".join(ch for ch in scope if ch.isalpha()).upper()
        filter_codes = {code} if code else None

    products: list[ProductRef] = []
    for code in sorted(masters.keys()):
        if filter_codes is not None and code not in filter_codes:
            continue
        item = masters[code]
        master_symbol = item.get("master")
        if not master_symbol:
            continue
        products.append(
            {
                "product_code": code,
                "symbol": str(master_symbol),
                "product_name": code,
            }
        )
        if len(products) >= max_products:
            break

    if not products:
        return [], "未匹配到可调研的主力合约"
    return products, None
