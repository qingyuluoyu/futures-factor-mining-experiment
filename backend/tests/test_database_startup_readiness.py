from pathlib import Path
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, event, text


class ReadinessConnection:
    def __init__(self, connection):
        self.connection = connection
        self.dialect = connection.dialect

    @asynccontextmanager
    async def begin(self):
        with self.connection.begin():
            yield

    async def execute(self, statement):
        return self.connection.execute(statement)

    async def run_sync(self, callback):
        return callback(self.connection)


class ReadinessEngine:
    def __init__(self):
        self.sync_engine = create_engine("sqlite:///:memory:")

    @asynccontextmanager
    async def connect(self):
        with self.sync_engine.connect() as connection:
            yield ReadinessConnection(connection)

    @asynccontextmanager
    async def begin(self):
        with self.sync_engine.begin() as connection:
            yield ReadinessConnection(connection)

    async def dispose(self):
        self.sync_engine.dispose()


def test_application_startup_never_runs_legacy_mutating_initialization():
    source = (Path(__file__).resolve().parents[1] / "app/main.py").read_text(encoding="utf-8")
    lifespan_source = source.split("async def lifespan(", 1)[1].split("def create_app(", 1)[0]
    assert "await _init_tables(" not in lifespan_source
    assert "await verify_database_schema(" in lifespan_source


@pytest.mark.asyncio
async def test_readiness_reports_missing_tables_and_columns_without_mutation():
    from app.services.database_readiness import SchemaReadinessError, verify_database_schema

    metadata = MetaData()
    Table("saved_assets", metadata, Column("id", Integer, primary_key=True), Column("amount", Integer))
    engine = ReadinessEngine()
    statements = []
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE saved_assets (id INTEGER PRIMARY KEY)"))
            await connection.execute(text("INSERT INTO saved_assets (id) VALUES (7)"))
        event.listen(engine.sync_engine, "before_cursor_execute", lambda connection, cursor, statement, parameters, context, many: statements.append(statement))
        with pytest.raises(SchemaReadinessError, match="saved_assets.amount"):
            await verify_database_schema(engine, metadata=metadata)
        Table("missing_table", metadata, Column("id", Integer, primary_key=True))
        with pytest.raises(SchemaReadinessError, match="missing_table"):
            await verify_database_schema(engine, metadata=metadata)
        assert not any(statement.lstrip().upper().startswith(("CREATE", "ALTER", "DROP", "UPDATE", "DELETE", "INSERT", "TRUNCATE")) for statement in statements)
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT id FROM saved_assets"))).scalar_one() == 7
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ready_existing_schema_is_checked_without_creating_or_rewriting_data():
    from app.services.database_readiness import verify_database_schema

    metadata = MetaData()
    Table("saved_assets", metadata, Column("id", Integer, primary_key=True), Column("amount", Integer))
    engine = ReadinessEngine()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
            await connection.execute(text("INSERT INTO saved_assets (id, amount) VALUES (7, 125)"))
        result = await verify_database_schema(engine, metadata=metadata)
        assert result == {"tables_checked": 1, "columns_checked": 2, "validation": "table_column_presence_only"}
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT amount FROM saved_assets"))).scalar_one() == 125
    finally:
        await engine.dispose()
