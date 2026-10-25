from uuid import uuid4

import pytest

from app.services.stock_research_jobs import JobError
from tests.test_stock_research_archive_v2 import page


def observations():
    return page(collected_at="2026-01-07T08:00:00Z", observations=[
        {"instrument_id": "000001.SZ", "effective_date": "2026-01-05", "values": {"close": "12.000000000000000001", "volume": "0.01"}},
        {"instrument_id": "000001.SZ", "effective_date": "2026-01-06", "values": {"close": "13", "volume": None}},
        {"instrument_id": "600000.SH", "effective_date": "2026-01-05", "values": {"close": "8", "volume": "0"}},
        {"instrument_id": "600000.SH", "effective_date": "2026-01-06", "values": {"close": "9"}},
    ])


def test_real_parquet_is_deterministic_precise_and_preserves_presence(tmp_path):
    from app.services.stock_research_v2.columnar_store import ParquetStore
    store = ParquetStore(tmp_path)
    source_id = uuid4()
    first = store.put_page(observations(), source_id=source_id, source_sha256="a" * 64, data_version="b" * 64)
    second = store.put_page(observations(), source_id=source_id, source_sha256="a" * 64, data_version="b" * 64)
    assert first == second
    assert (tmp_path / first.relative_path).read_bytes()[:4] == b"PAR1"
    batches = list(store.iter_batches(first, fields=["close", "volume"], batch_rows=2))
    assert all(len(batch) <= 2 for batch in batches)
    rows = [row for batch in batches for row in batch]
    assert [(r["effective_date"].isoformat(), r["instrument_id"]) for r in rows] == [
        ("2026-01-05", "000001.SZ"), ("2026-01-05", "600000.SH"), ("2026-01-06", "000001.SZ"), ("2026-01-06", "600000.SH")]
    assert rows[0]["close"] == "12.000000000000000001" and rows[0]["volume"] == "0.01"
    assert rows[1]["volume"] == "0" and rows[1]["volume__present"] is True
    assert rows[2]["volume"] is None and rows[2]["volume__present"] is True
    assert rows[3]["volume"] is None and rows[3]["volume__present"] is False
    assert not any("pb" in row for row in rows)


def test_corrupt_content_and_forged_source_binding_are_rejected(tmp_path):
    from app.services.stock_research_v2.columnar_store import ParquetStore
    store = ParquetStore(tmp_path)
    ref = store.put_page(observations(), source_id=uuid4(), source_sha256="a" * 64, data_version="b" * 64)
    forged = ref.model_copy(update={"source_sha256": "c" * 64})
    with pytest.raises(JobError):
        list(store.iter_batches(forged, fields=["close"]))
    (tmp_path / ref.relative_path).write_bytes(b"not-parquet")
    with pytest.raises(JobError) as error:
        list(store.iter_batches(ref, fields=["close"]))
    assert error.value.code == "columnar_integrity_failed"


def test_projection_and_batch_budget_are_validated_without_silent_changes(tmp_path):
    from app.services.stock_research_v2.columnar_store import ParquetStore
    store = ParquetStore(tmp_path)
    ref = store.put_page(observations(), source_id=uuid4(), source_sha256="a" * 64, data_version="b" * 64)
    for fields, batch_rows in [(["unknown"], 2), (["close", "close"], 2), (["close"], 0), (["close"], True), (["close"], 2049), (["roe"], 2)]:
        with pytest.raises(JobError):
            list(store.iter_batches(ref, fields=fields, batch_rows=batch_rows))


def test_financial_partition_keeps_original_disclosure_time_without_daily_alignment(tmp_path):
    from app.services.stock_research_v2.columnar_store import ParquetStore
    store = ParquetStore(tmp_path)
    financial = page(kind="financial", frequency="disclosure", financial_basis="consolidated", price_adjustment="not_applicable",
        units={"roe": "percent"}, observations=[{"instrument_id": "000001.SZ", "effective_date": "2026-01-05", "report_period": "2025-09-30",
            "announced_at": "2025-10-30T12:00:00+08:00", "available_at": "2025-10-30T04:00:00Z", "values": {"roe": "9.00001"}}])
    ref = store.put_page(financial, source_id=uuid4(), source_sha256="a" * 64, data_version="b" * 64)
    row = list(store.iter_batches(ref, fields=["roe"]))[0][0]
    assert row["roe"] == "9.00001" and row["announced_at"].isoformat() == "2025-10-30T04:00:00+00:00"
    assert row["report_period"].isoformat() == "2025-09-30"


def test_real_parquet_row_groups_and_projected_batches_remain_bounded(tmp_path):
    from app.services.stock_research_v2.columnar_store import ParquetStore, arrow_engine
    sample = page(collected_at="2026-01-07T08:00:00Z", units={"close": "CNY/share"}, observations=[
        {"instrument_id": f"{index:06d}.SZ", "effective_date": "2026-01-05", "values": {"close": "12.000000000000000001"}}
        for index in range(1, 1031)])
    store = ParquetStore(tmp_path)
    ref = store.put_page(sample, source_id=uuid4(), source_sha256="a" * 64, data_version="b" * 64)
    _, pq = arrow_engine()
    with pq.ParquetFile(tmp_path / ref.relative_path) as file:
        assert file.metadata.num_rows == 1030 and file.metadata.num_row_groups == 2
        assert all(file.metadata.row_group(index).num_rows <= 1024 for index in range(2))
    batches = list(store.iter_batches(ref, fields=["close"], batch_rows=128))
    assert sum(len(batch) for batch in batches) == 1030
    assert all(len(batch) <= 128 for batch in batches)
    assert all(row["close"] == "12.000000000000000001" and "volume" not in row for batch in batches for row in batch)
