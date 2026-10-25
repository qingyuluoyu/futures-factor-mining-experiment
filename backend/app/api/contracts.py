"""合约树 REST API —— 按品种代码分组的全部交割月份合约 + 主力识别

提供 GET /api/market/contracts/by-code 端点:
- 读取 Redis 中 market:contracts:master 缓存获取主力识别
- 用 generate_all_month_symbols 生成全部月份合约清单
- 读 Redis 中每个合约的 volume
- 组装树形 dict 返回,过滤无行情合约(volume=0 但保留)
"""

import json
import logging

from fastapi import APIRouter, Request
import redis.asyncio as aioredis

from app.data.contract_months import (
    generate_all_month_symbols,
    get_code_meta,
)
from app.schemas.contract_tree import CodeTreeSchema, ContractItemSchema
from app.services.contract_master import get_master_contracts

logger = logging.getLogger("qihuo.api.contracts")

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/contracts/by-code")
async def contracts_by_code(request: Request) -> dict[str, dict]:
    """按品种代码分组的合约树

    返回结构:{ "RB": { code, name, exchange, master, secondary, contracts: [...] }, ... }
    """
    redis_conn: aioredis.Redis = request.app.state.redis

    # 1. 读主力识别缓存
    master_map = await get_master_contracts(redis_conn)

    # 2. 生成全部月份合约清单
    from datetime import datetime, timezone, timedelta
    bj_now = datetime.now(timezone(timedelta(hours=8)))
    all_contracts = generate_all_month_symbols(bj_now)

    # 3. 读 Redis 中每个合约的 volume(用 pipeline 批量)
    pipe = redis_conn.pipeline()
    for c in all_contracts:
        pipe.get(f"market:quote:{c['symbol']}")
    quote_raws = await pipe.execute()

    # 4. 组装树形 dict
    tree: dict[str, dict] = {}
    for c, raw in zip(all_contracts, quote_raws):
        code = c["code"]
        symbol = c["symbol"]

        # 解析 quote 获取 volume
        volume = 0
        if raw:
            try:
                quote_data = json.loads(raw)
                volume = int(quote_data.get("volume", 0))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        # 初始化品种节点
        if code not in tree:
            meta = get_code_meta(code) or (c["name"], c["exchange"])
            name, exchange = meta
            tree[code] = {
                "code": code,
                "name": name,
                "exchange": exchange,
                "master": master_map.get(code, {}).get("master"),
                "secondary": master_map.get(code, {}).get("secondary"),
                "contracts": [],
            }

        # 判断主力/次主力标记
        master_symbol = master_map.get(code, {}).get("master")
        secondary_symbol = master_map.get(code, {}).get("secondary")
        is_master = (symbol == master_symbol)
        is_secondary = (symbol == secondary_symbol)

        # 添加合约项(volume=0 的也保留,供前端显示但 is_master/is_secondary 都 false)
        tree[code]["contracts"].append(ContractItemSchema(
            symbol=symbol,
            month=c["month"],
            year=c["year"],
            volume=volume,
            is_master=is_master,
            is_secondary=is_secondary,
        ).model_dump())

    return tree
