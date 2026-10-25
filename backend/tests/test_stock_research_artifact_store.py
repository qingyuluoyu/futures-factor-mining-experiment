from concurrent.futures import ThreadPoolExecutor
import hashlib

import pytest

from app.services.stock_research_v2.artifacts import ArtifactError, ArtifactStore


def test_content_addressed_write_preserves_precise_strings_and_same_version(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    payload = {"close": "12.000000000000000001", "volume": "0.01", "missing": None}
    first = store.put_json(payload)
    assert store.put_json(dict(reversed(list(payload.items())))) == first
    assert store.read_json(first.sha256) == payload
    assert first.byte_size > 0


def test_concurrent_identical_writes_are_idempotent(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.put_json({"actual": "QA-only"}), range(8)))
    assert len({row.sha256 for row in results}) == 1
    assert not list(store.root.glob("*.tmp"))


def test_corruption_unknown_hash_paths_nonfinite_and_oversize_fail(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts", max_bytes=100)
    ref = store.put_json({"value": "0.01"})
    (store.root / ref.relative_path).write_text('{}', encoding='utf8')
    with pytest.raises(ArtifactError): store.read_json(ref.sha256)
    for value in ["../secret", "a" * 64]:
        with pytest.raises(ArtifactError): store.read_json(value)
    for payload in [{"value": float("nan")}, {"value": "a" * 101}, {1: "ambiguous-key"}]:
        with pytest.raises(ArtifactError): store.put_json(payload)


@pytest.mark.parametrize("data", [b'{"value":1e309}', b'{"value":1,"value":2}'])
def test_valid_hash_does_not_make_invalid_or_noncanonical_json_safe(tmp_path, data):
    store = ArtifactStore(tmp_path / "artifacts")
    digest = hashlib.sha256(data).hexdigest()
    (store.root / (digest + ".json")).write_bytes(data)
    with pytest.raises(ArtifactError): store.read_json(digest)
