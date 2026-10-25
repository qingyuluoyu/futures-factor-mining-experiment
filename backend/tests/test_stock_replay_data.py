from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable


def source_rows():
    daily = [
        {"ts_code": "000001.SZ", "trade_date": "20261015", "open": 11.1,
         "high": 11.5, "low": 11, "close": 11.4, "vol": 1000.25},
        {"ts_code": "000001.SZ", "trade_date": "20261016", "open": "11.40",
         "high": "11.80", "low": "11.20", "close": "11.60", "vol": "2000"},
    ]
    factors = [
        {"ts_code": "000001.SZ", "trade_date": "20261015", "adj_factor": 100.01},
        {"ts_code": "000001.SZ", "trade_date": "20261016", "adj_factor": 100.01},
    ]
    limits = [
        {"ts_code": "000001.SZ", "trade_date": "20261015", "up_limit": 12.1, "down_limit": 9.9},
        {"ts_code": "000001.SZ", "trade_date": "20261016", "up_limit": 12.54, "down_limit": 10.26},
    ]
    calendar = [
        {"exchange": "SZSE", "cal_date": "20261015", "is_open": 1},
        {"exchange": "SZSE", "cal_date": "20261016", "is_open": 1},
    ]
    return daily, factors, limits, calendar


def normalize(rows=None, **kwargs):
    from app.services.stock_replay_data import normalize_replay_dataset

    return normalize_replay_dataset("000001", "平安银行", *(rows or source_rows()), **kwargs)


def test_normalizes_raw_prices_volume_and_traceable_metadata():
    fetched_at = datetime(2026, 10, 17, 1, 2, 3, tzinfo=timezone.utc)
    result = normalize(fetched_at=fetched_at)
    assert result["instrument_id"] == "000001.SZ"
    assert result["start_date"] == date(2026, 10, 15)
    assert result["end_date"] == date(2026, 10, 16)
    assert result["bars_json"][0] == {
        "trade_date": "2026-10-15", "open": "11.1", "high": "11.5", "low": "11",
        "close": "11.4", "volume_shares": 100025, "adj_factor": "100.01",
        "up_limit": "12.1", "down_limit": "9.9",
    }
    meta = result["meta"]
    assert meta["supplier"] == "daoshare"
    assert meta["market"] == "stock"
    assert meta["exchange"] == "SZSE"
    assert meta["currency"] == "CNY"
    assert meta["timezone"] == "Asia/Shanghai"
    assert meta["adjustment"] == "none"
    assert meta["volume_unit"] == "share"
    assert meta["frequency"] == "1d"
    assert meta["calendar_dates"] == ["2026-10-15", "2026-10-16"]
    assert meta["source_apis"] == ["daily", "adj_factor", "stk_limit", "trade_cal"]
    assert meta["fetched_at"] == fetched_at.isoformat()
    assert meta["schema_version"] == 1
    assert meta["authorization_boundary"]
    assert meta["data_version"] == result["version"]
    assert len(result["version"]) == 64


def test_version_is_stable_for_reordered_equivalent_source_and_new_fetch_time():
    first = normalize(fetched_at=datetime(2026, 10, 17, tzinfo=timezone.utc))
    rows = source_rows()
    for records in rows:
        records.reverse()
    rows[0][1]["open"] = "11.1000"
    second = normalize(rows, fetched_at=datetime(2026, 10, 18, tzinfo=timezone.utc))
    assert first["version"] == second["version"]
    rows[0][0]["close"] = "11.61"
    assert normalize(rows)["version"] != first["version"]


@pytest.mark.parametrize("source_index", [0, 1, 2, 3])
def test_rejects_missing_open_day_in_any_required_source(source_index):
    rows = source_rows()
    rows[source_index].pop()
    with pytest.raises(ValueError, match="missing|coverage|calendar"):
        normalize(rows, start_date="20261015", end_date="20261016")


@pytest.mark.parametrize("source_index", [0, 1, 2, 3])
def test_rejects_duplicate_dates_in_any_required_source(source_index):
    rows = source_rows()
    rows[source_index].append(deepcopy(rows[source_index][0]))
    with pytest.raises(ValueError, match="duplicate"):
        normalize(rows)


@pytest.mark.parametrize("source_index,field,value", [
    (0, "open", None), (0, "close", "NaN"), (0, "low", "Infinity"),
    (0, "high", "10"), (0, "open", "0"), (0, "vol", "-1"),
    (0, "vol", "0.001"), (0, "ts_code", "600000.SH"),
    (1, "adj_factor", None), (1, "adj_factor", "0"), (1, "adj_factor", "NaN"),
    (2, "up_limit", None), (2, "down_limit", "0"), (2, "up_limit", "9"),
    (2, "down_limit", "11.2"), (3, "is_open", None), (3, "is_open", 2),
    (3, "exchange", "SSE"),
])
def test_rejects_invalid_values_without_financial_defaults(source_index, field, value):
    rows = source_rows()
    rows[source_index][0][field] = value
    with pytest.raises(ValueError):
        normalize(rows)


@pytest.mark.parametrize("code", ["300001", "688001", "830001", "000001.SH", "abc", "1"])
def test_only_accepts_standard_main_board_instruments(code):
    from app.services.stock_replay_data import normalize_replay_dataset

    with pytest.raises(ValueError, match="main.board|instrument|code"):
        normalize_replay_dataset(code, "名称", *source_rows())


def test_normalizes_shanghai_identifier_and_filters_closed_calendar_dates():
    from app.services.stock_replay_data import normalize_replay_dataset

    rows = source_rows()
    for records in rows[:3]:
        for record in records:
            record["ts_code"] = "600036.SH"
    for record in rows[3]:
        record["exchange"] = "SSE"
    rows[3].append({"exchange": "SSE", "cal_date": "20261017", "is_open": 0})
    result = normalize_replay_dataset("600036.SH", "招商银行", *rows)
    assert result["code"] == "600036"
    assert result["instrument_id"] == "600036.SH"
    assert result["meta"]["exchange"] == "SSE"


def test_explicit_range_does_not_ignore_missing_first_or_last_open_day():
    rows = source_rows()
    rows[0].pop(0)
    with pytest.raises(ValueError, match="coverage|missing"):
        normalize(rows, start_date=date(2026, 10, 15), end_date=date(2026, 10, 16))


def test_replay_models_are_isolated_and_match_contract():
    from app.models.base import Base
    from app.models.stock_replay import (
        ReplayBase, StockReplayAccount, StockReplayDataset, StockReplayOrder, StockReplayStep,
    )

    assert ReplayBase.metadata is not Base.metadata
    expected_tables = {
        "stock_replay_datasets", "stock_replay_accounts", "stock_replay_orders", "stock_replay_steps",
    }
    assert set(ReplayBase.metadata.tables) == expected_tables
    assert not expected_tables.intersection(Base.metadata.tables)
    assert StockReplayAccount.__table__.c.realized_pnl.type.scale == 2
    assert StockReplayAccount.__table__.c.fees_paid.type.scale == 2
    assert StockReplayOrder.__table__.c.filled_price.type.scale == 4
    assert StockReplayAccount.__table__.c.created_at.type.timezone
    assert StockReplayDataset.__table__.c.version.unique
    step_table = StockReplayStep.__table__
    step_unique = [tuple(column.name for column in constraint.columns)
                   for constraint in step_table.constraints if isinstance(constraint, UniqueConstraint)]
    assert ("account_id", "idempotency_key") in step_unique
    assert ("account_id", "from_revision") in step_unique
    assert any("advance" in str(constraint.sqltext) and "cancel" in str(constraint.sqltext)
               for constraint in step_table.constraints if isinstance(constraint, CheckConstraint))
    for table in ReplayBase.metadata.sorted_tables:
        assert "CREATE TABLE stock_replay_" in str(CreateTable(table).compile(dialect=postgresql.dialect()))


def test_account_date_constraint_uses_column_instead_of_database_current_date():
    from app.models.stock_replay import StockReplayAccount

    date_constraint = next(value for value in StockReplayAccount.__table__.constraints
                           if value.name == "ck_stock_replay_account_dates")
    assert 'start_date <= "current_date" AND "current_date" <= end_date' == str(date_constraint.sqltext)


def replay_sqlite_engine():
    from app.models.stock_replay import ReplayBase

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id UUID PRIMARY KEY)"))
    ReplayBase.metadata.create_all(engine)
    return engine


async def test_persist_snapshot_is_immutable_and_same_version_is_not_inserted_twice():
    from app.models.stock_replay import StockReplayDataset
    from app.services.stock_replay_data import persist_replay_dataset

    engine = replay_sqlite_engine()
    first = normalize(fetched_at=datetime(2026, 10, 17, tzinfo=timezone.utc))
    second = normalize(fetched_at=datetime(2026, 10, 18, tzinfo=timezone.utc))
    with Session(engine) as sync_session:
        class Adapter:
            async def execute(self, statement):
                return sync_session.execute(statement)

        saved, created = await persist_replay_dataset(Adapter(), first)
        saved_id = saved.id
        assert created is True
        sync_session.commit()
        saved_again, created_again = await persist_replay_dataset(Adapter(), second)
        assert created_again is False
        assert saved_again.id == saved_id
        assert saved_again.meta["fetched_at"] == first["meta"]["fetched_at"]
        assert sync_session.query(StockReplayDataset).count() == 1
        sync_session.commit()
    engine.dispose()


def test_database_rejects_limit_order_without_limit_price():
    from uuid import uuid4

    from app.models.stock_replay import StockReplayAccount, StockReplayOrder

    engine = replay_sqlite_engine()
    with Session(engine) as session:
        account = StockReplayAccount(
            user_id=uuid4(), trading_mode="virtual", name="隔离测试", initial_cash=Decimal("10000"),
            cash=Decimal("10000"), start_date=date(2026, 10, 15), current_date=date(2026, 10, 15),
            end_date=date(2026, 10, 16), datasets_json={}, settings_json={}, create_key=uuid4(), params_hash="0" * 64,
        )
        session.add(account)
        session.flush()
        session.add(StockReplayOrder(
            account_id=account.id, idempotency_key=uuid4(), params_hash="0" * 64,
            code="000001", direction="buy", quantity=100, order_type="limit",
            limit_price=None, submitted_on=date(2026, 10, 15),
        ))
        with pytest.raises(IntegrityError):
            session.flush()
    engine.dispose()


@pytest.mark.parametrize("source_index,field,value", [
    (0, "open", "1e-99999"), (1, "adj_factor", "1e-99999"), (2, "down_limit", "1e-99999"),
])
def test_rejects_unbounded_numeric_precision(source_index, field, value):
    rows = source_rows()
    rows[source_index][0][field] = value
    with pytest.raises(ValueError):
        normalize(rows)


def test_rejects_truncated_official_calendar_even_if_all_sources_are_equally_truncated():
    rows = source_rows()
    for records in rows:
        records.pop()
    with pytest.raises(ValueError, match="calendar|coverage|missing"):
        normalize(rows, start_date="20261015", end_date="20261016")


def test_snapshot_dates_use_first_and_last_bar_when_requested_range_includes_closed_days():
    rows = source_rows()
    rows[3].append({"exchange": "SZSE", "cal_date": "20261017", "is_open": 0})
    result = normalize(rows, start_date="20261015", end_date="20261017")
    assert result["start_date"] == date(2026, 10, 15)
    assert result["end_date"] == date(2026, 10, 16)
    assert result["version"] == normalize()["version"]


def load_import_cli():
    path = Path(__file__).resolve().parents[2] / "scripts" / "import-stock-replay-data.py"
    spec = spec_from_file_location("stock_replay_import_test", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_import_cli_enforces_code_count_and_calendar_day_boundaries():
    module = load_import_cli()
    assert module.validate_import_request("000001,600036,600519", "20260901", "20261016") == (
        ["000001", "600036", "600519"], date(2026, 9, 1), date(2026, 10, 16),
    )
    with pytest.raises(ValueError, match="20"):
        module.validate_import_request(",".join(f"600{index:03d}" for index in range(21)), "20260901", "20261016")
    with pytest.raises(ValueError, match="366"):
        module.validate_import_request("000001", "20240101", "20261016")
    with pytest.raises(ValueError):
        module.validate_import_request("000001,000001", "20260901", "20261016")
    with pytest.raises(ValueError):
        module.validate_import_request("300001", "20260901", "20261016")


def test_import_cli_makes_bounded_raw_source_calls_and_resolves_name():
    module = load_import_cli()

    class Client:
        def __init__(self):
            self.calls = []

        def query(self, api_name, params=None, fields=""):
            self.calls.append((api_name, params, fields))
            if api_name == "stock_basic":
                return [{"ts_code": "000001.SZ", "name": "平安银行", "market": "主板"}]
            return dict(zip(["daily", "adj_factor", "stk_limit", "trade_cal"], source_rows()))[api_name]

    client = Client()
    result = module.fetch_replay_snapshot(client, "000001", date(2026, 10, 15), date(2026, 10, 16))
    assert result["name"] == "平安银行"
    assert [call[0] for call in client.calls] == ["stock_basic", "daily", "adj_factor", "stk_limit", "trade_cal"]
    assert client.calls[-1][1]["exchange"] == "SZSE"
    assert result["bars_json"][0]["open"] == "11.1"


def test_import_cli_rejects_st_and_nonordinary_source_name():
    module = load_import_cli()

    class Client:
        def query(self, api_name, params=None, fields=""):
            assert api_name == "stock_basic"
            return [{"ts_code": "000001.SZ", "name": "*ST样例", "market": "主板"}]

    with pytest.raises(ValueError, match="ST|ordinary"):
        module.fetch_replay_snapshot(Client(), "000001", date(2026, 10, 15), date(2026, 10, 16))
