"""一次性迁移：因子 token 编码 v1 → v2（冻结特征 id 空间）

背景：v1 编码里算子 id = 算子序号 + len(FEATURE_NAMES) = 算子序号 + 14，
新增特征会整体平移算子 id，已持久化的公式会被解码成完全不同的算子。
v2 把特征 id 空间冻结为 64，特征与算子从此各自独立增长。
详见 backend/app/services/factor_lab/token_encoding.py。

覆盖三处持久化：
  - factor_history.tokens（同时重算 tokens_key 与 text）
  - factor_favorites.tokens（同时重算 tokens_key 与 text）
  - ai_trading_tasks.strategy_params->factor_tokens

幂等：以 system_settings.factor_token_encoding_version 为权威判据，
已是 2 则直接退出；迁移与置版本在同一事务内完成。

必须在部署含 v2 代码的镜像**之后**执行。未迁移时后端会拒绝执行存量公式
（返回 409），而不是按错编码静默解码。

用法（在 backend 容器内，或本地 backend 目录）:
  uv run python scripts/migrate-factor-token-encoding.py
  uv run python scripts/migrate-factor-token-encoding.py --dry-run   # 只统计不改
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

# 把 backend 加入 import 路径
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND_ROOT not in sys.path and os.path.isdir(_BACKEND_ROOT):
    sys.path.insert(0, _BACKEND_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("migrate_factor_token_encoding")


async def _migrate_token_table(conn: Any, table: str, dry_run: bool) -> int:
    """迁移一张带 tokens/tokens_key/text 的表，返回改动行数"""
    from sqlalchemy import text as sql

    from app.services.factor_lab.express import to_text
    from app.services.factor_lab.persist.tokens_codec import tokens_key
    from app.services.factor_lab.token_encoding import remap_v1_to_v2

    rows = (await conn.execute(sql(f"SELECT id, tokens FROM {table}"))).fetchall()
    changed = 0
    for row in rows:
        old_tokens = [int(t) for t in (row[1] or [])]
        if not old_tokens:
            continue
        new_tokens = remap_v1_to_v2(old_tokens)
        if new_tokens == old_tokens:
            continue
        changed += 1
        if dry_run:
            continue
        await conn.execute(
            sql(
                f"UPDATE {table} SET tokens = CAST(:tk AS jsonb), "
                "tokens_key = :key, text = :txt WHERE id = :id"
            ),
            {
                "tk": json.dumps(new_tokens),
                "key": tokens_key(new_tokens),
                "txt": to_text(new_tokens),
                "id": str(row[0]),
            },
        )
    return changed


async def _migrate_ai_tasks(conn: Any, dry_run: bool) -> int:
    """迁移 ai_trading_tasks.strategy_params->factor_tokens，返回改动行数"""
    from sqlalchemy import text as sql

    from app.services.factor_lab.token_encoding import remap_v1_to_v2

    rows = (
        await conn.execute(
            sql(
                "SELECT id, strategy_params FROM ai_trading_tasks "
                "WHERE strategy_params ? 'factor_tokens'"
            )
        )
    ).fetchall()
    changed = 0
    for row in rows:
        params = dict(row[1] or {})
        old_tokens = [int(t) for t in (params.get("factor_tokens") or [])]
        if not old_tokens:
            continue
        new_tokens = remap_v1_to_v2(old_tokens)
        if new_tokens == old_tokens:
            continue
        changed += 1
        if dry_run:
            continue
        params["factor_tokens"] = new_tokens
        await conn.execute(
            sql(
                "UPDATE ai_trading_tasks SET strategy_params = CAST(:p AS jsonb) "
                "WHERE id = :id"
            ),
            {"p": json.dumps(params), "id": str(row[0])},
        )
    return changed


async def migrate(dry_run: bool) -> dict[str, Any]:
    from sqlalchemy import text as sql

    from app.core.config import load_settings
    from app.core.database import create_database_engine
    from app.services.factor_lab.token_encoding import TOKEN_ENCODING_VERSION

    settings = load_settings()
    engine = create_database_engine(settings)
    try:
        async with engine.begin() as conn:
            has_column = (
                await conn.execute(
                    sql(
                        "SELECT COUNT(*) FROM information_schema.columns "
                        "WHERE table_name = 'system_settings' "
                        "AND column_name = 'factor_token_encoding_version'"
                    )
                )
            ).scalar()
            if not has_column:
                return {"missing_column": True}

            current = (
                await conn.execute(
                    sql(
                        "SELECT COALESCE(MAX(factor_token_encoding_version), 1) "
                        "FROM system_settings"
                    )
                )
            ).scalar()
            current = int(current or 1)
            if current >= TOKEN_ENCODING_VERSION:
                return {"skipped": True, "version": current}

            history = await _migrate_token_table(conn, "factor_history", dry_run)
            favorites = await _migrate_token_table(conn, "factor_favorites", dry_run)
            tasks = await _migrate_ai_tasks(conn, dry_run)

            if not dry_run:
                await conn.execute(
                    sql(
                        "UPDATE system_settings "
                        "SET factor_token_encoding_version = :v"
                    ),
                    {"v": TOKEN_ENCODING_VERSION},
                )
            return {
                "skipped": False,
                "from_version": current,
                "to_version": TOKEN_ENCODING_VERSION,
                "factor_history": history,
                "factor_favorites": favorites,
                "ai_trading_tasks": tasks,
            }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="因子 token 编码 v1 → v2 迁移")
    parser.add_argument("--dry-run", action="store_true", help="只统计不修改")
    args = parser.parse_args()

    result = asyncio.run(migrate(args.dry_run))
    if result.get("missing_column"):
        logger.error(
            "system_settings 缺少 factor_token_encoding_version 列。"
            "该列由后端启动时补齐，请先部署含 v2 编码的后端镜像再执行本脚本。"
        )
        sys.exit(1)
    if result.get("skipped"):
        logger.info("已是 v%s 编码，无需迁移", result["version"])
        return
    action = "将迁移" if args.dry_run else "已迁移"
    logger.info(
        "v%s → v%s：%s factor_history %d 行、factor_favorites %d 行、"
        "ai_trading_tasks %d 行",
        result["from_version"],
        result["to_version"],
        action,
        result["factor_history"],
        result["factor_favorites"],
        result["ai_trading_tasks"],
    )
    if args.dry_run:
        logger.info("dry-run 未写入，版本号保持 v%s", result["from_version"])


if __name__ == "__main__":
    main()
