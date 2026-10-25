import importlib

from sqlalchemy import MetaData, inspect, text

from app.models.base import Base


MODEL_MODULES = (
    "user", "watchlist", "ai_provider", "ai_model", "ai_conversation", "ai_skill",
    "paper", "ai_trading", "ai_trading.run_log", "system_settings", "factor_lab",
    "big_order", "task_alert", "factor_mining", "kline_minute", "stock", "api_key",
    "strategy_folder", "ai_trading.favorite", "ai_anchor",
)


class SchemaReadinessError(RuntimeError):
    pass


async def verify_database_schema(engine, *, metadata: MetaData | None = None) -> dict:
    if metadata is None:
        for module in MODEL_MODULES:
            importlib.import_module(f"app.models.{module}")
        metadata = Base.metadata
    if not metadata.tables:
        raise SchemaReadinessError("No registered schema; refusing automatic database initialization")

    def check(connection):
        inspector = inspect(connection)
        columns = inspector.get_multi_columns(filter_names=[table.name for table in metadata.tables.values()])
        observed = {name: {column["name"] for column in values} for (_, name), values in columns.items()}
        missing = []
        for table in metadata.tables.values():
            if table.name not in observed:
                missing.append(table.name)
            else:
                missing.extend(f"{table.name}.{column.name}" for column in table.columns if column.name not in observed[table.name])
        if missing:
            details = ", ".join(sorted(missing)[:25])
            raise SchemaReadinessError(f"Database schema is incompatible: {details}; run reviewed versioned migrations before starting; no data was changed")
        return {"tables_checked": len(metadata.tables), "columns_checked": sum(len(table.columns) for table in metadata.tables.values()), "validation": "table_column_presence_only"}

    async with engine.connect() as connection, connection.begin():
        if connection.dialect.name == "postgresql":
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
            await connection.execute(text("SET LOCAL lock_timeout = '2s'"))
        return await connection.run_sync(check)
