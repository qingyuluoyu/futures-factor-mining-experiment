"""The study-input transport must move real panels losslessly and refuse what it cannot bound."""
from collections.abc import Mapping
import json

import numpy as np
from pandas import DataFrame, Index
import pytest

from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2 import study_transport
from app.services.stock_research_v2.artifacts import ArtifactError, ArtifactStore
from app.services.stock_research_v2.study_transport import (INDEX_LIMIT, PANEL_LIMIT, TRANSPORT_VERSION, PanelRef,
    StudyPayload, StudyRef, materialize, reference_digest, restore)

DATES = ["2026-01-05", "2026-01-06", "2026-01-07"]
CODES = ["000001.SZ", "600000.SH"]
METADATA = {"method_version": "stock-columnar-study-inputs-v1", "qualification": "observation_only",
    "input_sha256": "a" * 64, "calendar_version": "cal-1", "pool_version": "pool-1"}
ELIGIBLE = np.array([[True, False], [False, True], [True, True]])


def panel(values=None):
    # A study grid is NaN wherever a disclosure is missing.  JSON cannot carry that at
    # all (``allow_nan=False``), so this is exactly the case the binary path exists for.
    if values is None:
        values = np.array([[10.5, np.nan], [np.nan, 3.25], [0.0, -1.75]])
    return DataFrame(np.asarray(values, dtype="float64"), index=Index(DATES), columns=Index(CODES))


def payload(**overrides):
    return StudyPayload(fields=overrides.pop("fields", {"close": panel(), "pb": panel()}),
        eligible=overrides.pop("eligible", panel(ELIGIBLE)), metadata=overrides.pop("metadata", dict(METADATA)))


def test_a_study_grid_round_trips_byte_exactly_including_the_nan_cells(tmp_path):
    original = payload()
    reference = materialize(original, ArtifactStore(tmp_path))
    assert reference.transport_version == TRANSPORT_VERSION
    assert reference.eligible.byte_size > 0
    restored = restore(reference, ArtifactStore(tmp_path))
    for name, frame in original.fields.items():
        assert list(restored.fields[name].index) == DATES
        assert list(restored.fields[name].columns) == CODES
        # NaN must survive as NaN: a filled or rounded cell is a fabricated disclosure.
        assert np.array_equal(restored.fields[name].to_numpy(), frame.to_numpy(), equal_nan=True)
    assert np.array_equal(restored.eligible.to_numpy(), ELIGIBLE)
    assert restored.metadata == METADATA


def test_a_restored_panel_is_a_fresh_read_and_editing_it_changes_nothing(tmp_path):
    store = ArtifactStore(tmp_path)
    reference = materialize(payload(), store)
    first = restore(reference, store)
    first.fields["close"].iloc[0, 0] = 999.0
    assert restore(reference, store).fields["close"].iloc[0, 0] == 10.5


def test_the_reference_digest_is_stable_and_binds_the_inputs_a_report_used(tmp_path):
    store = ArtifactStore(tmp_path)
    reference = materialize(payload(), store)
    assert reference_digest(reference) == reference_digest(materialize(payload(), store))
    assert reference_digest(reference) == reference_digest(json.loads(reference.model_dump_json()))
    other = materialize(payload(metadata={**METADATA, "pool_version": "pool-2"}), store)
    assert reference_digest(other) != reference_digest(reference)


def test_a_reference_naming_another_transport_version_is_refused(tmp_path):
    reference = materialize(payload(), ArtifactStore(tmp_path))
    with pytest.raises(JobError) as failure:
        restore(reference.model_copy(update={"transport_version": "stock-v2-study-transport-v0"}), ArtifactStore(tmp_path))
    assert failure.value.code == "study_reference_invalid"


def test_a_reference_assembled_without_validation_is_refused_as_a_reference_not_as_content(tmp_path):
    store = ArtifactStore(tmp_path)
    reference = materialize(payload(), store)
    # ``model_construct`` is the documented way to build a pydantic model while
    # skipping validation, which is what a half-built parent object looks like.  The
    # child must call the *reference* malformed rather than go hunting for content
    # under a digest that is not a digest at all.
    broken = StudyRef.model_construct(transport_version=TRANSPORT_VERSION,
        fields={"close": PanelRef.model_construct(sha256="not-a-digest", byte_size=8, rows=1, columns=1,
            dates=["2026-01-05"], instruments=["000001.SZ"])},
        eligible=reference.eligible, metadata={})
    with pytest.raises(JobError) as failure:
        restore(broken, store)
    assert failure.value.code == "study_reference_invalid"


def test_a_reference_describing_a_shape_the_bytes_do_not_have_is_refused(tmp_path):
    store = ArtifactStore(tmp_path)
    reference = materialize(payload(fields={"close": panel()}), store)
    wrong = json.loads(reference.model_dump_json())
    wrong["fields"]["close"]["rows"] = 9
    with pytest.raises(JobError) as failure:
        restore(wrong, store)
    assert failure.value.code == "study_panel_invalid"


def test_a_panel_whose_axes_collide_is_refused_rather_than_silently_merged(tmp_path):
    frame = DataFrame(np.zeros((2, 2), dtype="float64"), index=Index(["2026-01-05", "2026-01-05"]), columns=Index(CODES))
    with pytest.raises(JobError) as failure:
        materialize(payload(fields={"close": frame}), ArtifactStore(tmp_path))
    assert failure.value.code == "study_axis_ambiguous"


def test_a_panel_with_a_blank_axis_label_is_refused(tmp_path):
    frame = DataFrame(np.zeros((2, 2), dtype="float64"), index=Index(["2026-01-05", " "]), columns=Index(CODES))
    with pytest.raises(JobError) as failure:
        materialize(payload(fields={"close": frame}), ArtifactStore(tmp_path))
    assert failure.value.code == "study_axis_invalid"


def test_a_study_with_no_field_panel_is_refused_before_anything_is_stored(tmp_path):
    root = tmp_path / "root"
    with pytest.raises(JobError) as failure:
        materialize(payload(fields={}), ArtifactStore(root))
    assert failure.value.code == "study_payload_invalid"
    assert list(root.iterdir()) == []


def test_a_panel_that_is_not_numeric_is_refused_rather_than_coerced(tmp_path):
    frame = DataFrame([["a", "b"], ["c", "d"], ["e", "f"]], index=Index(DATES), columns=Index(CODES))
    with pytest.raises(JobError) as failure:
        materialize(payload(fields={"close": frame}), ArtifactStore(tmp_path))
    assert failure.value.code == "study_panel_invalid"


def test_a_tampered_payload_fails_the_stores_own_digest_check(tmp_path):
    store = ArtifactStore(tmp_path)
    reference = materialize(payload(), store)
    store._path(reference.fields["close"].sha256, ".bin").write_bytes(b"not the panel the reference names")
    with pytest.raises(JobError) as failure:
        restore(reference, store)
    assert failure.value.code == "study_panel_invalid"


def test_an_oversize_panel_is_refused_under_a_name_the_caller_can_act_on(tmp_path, monkeypatch):
    # The real ceiling is 16MiB; the refusal path is the same one, and a 2M-row frame
    # would spend its time in str() rather than in anything the test is about.
    monkeypatch.setattr(study_transport, "PANEL_LIMIT", 4096)
    wide = DataFrame(np.zeros((600, 1), dtype="float64"), index=Index([str(i) for i in range(600)]), columns=Index(CODES[:1]))
    root = tmp_path / "root"
    with pytest.raises(JobError) as failure:
        materialize(payload(fields={"close": wide}), ArtifactStore(root))
    assert failure.value.code == "study_panel_invalid"
    assert list(root.iterdir()) == []


def test_a_payload_beyond_the_binary_ceiling_is_refused_by_the_store_itself(tmp_path):
    store = ArtifactStore(tmp_path)
    with pytest.raises(ArtifactError):
        store.put_binary(b"x" * (32 * 1024 * 1024 + 1))


def test_a_reference_that_is_not_even_a_mapping_is_refused(tmp_path):
    store = ArtifactStore(tmp_path)
    materialize(payload(), store)
    for candidate in ("not json", 7, ["a"], {"fields": 1}):
        with pytest.raises(JobError) as failure:
            restore(candidate, store)
        assert failure.value.code == "study_reference_invalid"


def test_a_reference_must_name_every_panel_it_will_read(tmp_path):
    store = ArtifactStore(tmp_path)
    reference = materialize(payload(), store)
    stripped = json.loads(reference.model_dump_json())
    del stripped["eligible"]
    with pytest.raises(JobError) as failure:
        restore(stripped, store)
    assert failure.value.code == "study_reference_invalid"


def test_the_index_budget_keeps_a_reference_small_enough_to_travel_over_the_wire(tmp_path):
    reference = materialize(payload(), ArtifactStore(tmp_path))
    assert INDEX_LIMIT == 1 * 1024 * 1024
    assert len(reference.model_dump_json()) < INDEX_LIMIT
    assert isinstance(reference.metadata, Mapping)
