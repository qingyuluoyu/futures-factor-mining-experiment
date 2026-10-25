"""信号扫描宇宙 —— 全部主力合约（中文品种名）"""

from __future__ import annotations

import logging
from typing import Any

from app.data.contracts import get_contract_name, get_product_name
from app.services.contract_master import get_master_contracts

logger = logging.getLogger("qihuo.signal_universe")


async def list_all_master_symbols(redis_conn: Any) -> list[dict[str, str]]:
    """列出 Redis 中全部主力合约。

    返回 [{product_code, symbol, name, product_name, contract_name}]
    name / product_name 为中文品种名；contract_name 含月份。
    """
    if redis_conn is None:
        return []
    masters = await get_master_contracts(redis_conn)
    if not masters:
        try:
            from app.services.contract_master import cache_master_contracts

            masters = await cache_master_contracts(redis_conn)
        except Exception:
            logger.exception("主力合约识别失败")
            return []

    items: list[dict[str, str]] = []
    for code in sorted(masters.keys()):
        entry = masters[code] or {}
        master = entry.get("master")
        if not master:
            continue
        code_u = str(code).upper()
        symbol = str(master).strip().lower()
        product_name = get_product_name(code_u)
        contract_name = get_contract_name(symbol)
        items.append(
            {
                "product_code": code_u,
                "symbol": symbol,
                "name": product_name,
                "product_name": product_name,
                "contract_name": contract_name,
            }
        )
    return items
