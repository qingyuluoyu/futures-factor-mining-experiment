from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def isolated_connector_environment(monkeypatch):
    monkeypatch.delenv("SINA_REPLICATE_REDIS_URL", raising=False)
    monkeypatch.delenv("SINA_BRIDGE_PORT", raising=False)


def test_preview_connector_accepts_only_dedicated_local_environment(monkeypatch):
    from scripts.stock_preview_connector import verify_environment

    monkeypatch.delenv("SINA_REPLICATE_REDIS_URL", raising=False)
    monkeypatch.delenv("SINA_BRIDGE_PORT", raising=False)
    verify_environment(SimpleNamespace(redis_host="127.0.0.1", redis_port=6380))


@pytest.mark.parametrize("host,port", [("localhost", 6380), ("127.0.0.1", 6379), ("example.org", 6380)])
def test_preview_connector_rejects_non_dedicated_redis(host, port):
    from scripts.stock_preview_connector import verify_environment

    with pytest.raises(RuntimeError):
        verify_environment(SimpleNamespace(redis_host=host, redis_port=port))


@pytest.mark.parametrize("name,value", [("SINA_REPLICATE_REDIS_URL", "redis://example.org:6379/0"), ("SINA_BRIDGE_PORT", "8120")])
def test_preview_connector_rejects_remote_replication_and_port_drift(monkeypatch, name, value):
    from scripts.stock_preview_connector import verify_environment

    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError):
        verify_environment(SimpleNamespace(redis_host="127.0.0.1", redis_port=6380))


def test_preview_connector_keeps_original_factory_and_only_loopback(monkeypatch):
    import uvicorn
    from scripts import stock_preview_connector as module
    from app.core import config

    configured, checked, launches = [], [], []
    monkeypatch.setattr(module, "configure_environment", lambda path: configured.append(path))
    monkeypatch.setattr(module, "verify_environment", lambda settings: checked.append(settings))
    settings = SimpleNamespace(redis_host="127.0.0.1", redis_port=6380)
    monkeypatch.setattr(config, "load_settings", lambda: settings)
    monkeypatch.setattr(uvicorn, "run", lambda application, **options: launches.append((application, options)))
    module.run_connector()
    assert len(configured) == 1 and checked == [settings]
    assert launches == [("app.connectors.sina.api:create_app", {"factory": True, "host": "127.0.0.1", "port": 8110, "access_log": False})]
