from datetime import datetime, timezone
from importlib import import_module
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.user import User


def research_models():
    return import_module("app.models.stock_research_job")


def test_research_metadata_is_isolated_and_uuid_foreign_key_is_restricted():
    module = research_models()
    table = module.StockResearchJob.__table__
    assert table.metadata is module.ResearchJobBase.metadata
    assert "stock_research_jobs" not in Base.metadata.tables
    assert str(table.c.request_hash.type) == "CHAR(64)"
    foreign_key = next(iter(table.c.user_id.foreign_keys))
    assert foreign_key.column is User.__table__.c.id
    assert foreign_key.ondelete == "RESTRICT"
    assert {tuple(column.name for column in index.columns) for index in table.indexes} == {
        ("user_id", "trading_mode", "created_at", "id"),
        ("status", "created_at"), ("status", "lease_expires_at"),
    }


@pytest.fixture
def job_database():
    module = research_models()
    engine = create_engine("sqlite:///:memory:")
    User.__table__.create(engine)
    module.ResearchJobBase.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(username="research_schema_test", hashed_password="test-only")
        session.add(user)
        session.commit()
        yield module, session, user.id
    engine.dispose()


def new_job(module, user_id, **changes):
    values = dict(user_id=user_id, trading_mode="virtual", kind="analysis", title="测试研究",
                  request_id="a" * 32, idempotency_key=uuid4(), request_hash="a" * 64,
                  params_json={}, method_version="method-v1", workflow_version="workflow-v1")
    values.update(changes)
    return module.StockResearchJob(**values)


def test_pending_defaults_and_scoped_idempotency(job_database):
    module, session, user_id = job_database
    job = new_job(module, user_id)
    session.add(job)
    session.commit()
    assert job.status == "pending" and job.progress == 0 and job.attempts == 0
    assert job.data_manifest == {} and job.result_json is None and job.result_bytes == 0
    session.add(new_job(module, user_id, idempotency_key=job.idempotency_key))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.add(new_job(module, user_id, trading_mode="live", idempotency_key=job.idempotency_key))
    session.commit()


@pytest.mark.parametrize("changes", [
    {"status": "unknown"}, {"trading_mode": "other"}, {"kind": "trade"},
    {"progress": -1}, {"progress": 101}, {"attempts": 2}, {"attempts": -1},
    {"result_bytes": -1}, {"result_bytes": 8388609},
    {"result_json": {}}, {"status": "running"},
    {"status": "completed", "finished_at": datetime.now(timezone.utc)},
    {"status": "failed"}, {"status": "cancelled"}, {"status": "expired"},
])
def test_database_rejects_invalid_state_payloads(job_database, changes):
    module, session, user_id = job_database
    session.add(new_job(module, user_id, **changes))
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "expired", "running"])
def test_database_accepts_complete_state_payloads(job_database, status):
    module, session, user_id = job_database
    now = datetime.now(timezone.utc)
    values = {"status": status}
    if status == "running":
        values.update(worker_token=uuid4(), started_at=now, deadline_at=now, lease_expires_at=now)
    else:
        values["finished_at"] = now
    if status == "completed":
        values.update(result_json={"market": "stock"}, data_version="b" * 64, result_bytes=18)
    session.add(new_job(module, user_id, **values))
    session.commit()
