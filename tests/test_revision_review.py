"""Independent JSON sidecar acceptance and failure-path regressions.

Fixtures contain synthetic bytes and artificial samples, never patient data.
"""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import math
from pathlib import Path

import pytest

from signal_annotation.adapters import storage
from signal_annotation.adapters.storage import (
    ConflictError, JsonSidecarRepository, StorageError, from_dict, sidecar_path, to_dict,
)
from signal_annotation.application import AnnotationService
from signal_annotation.domain import (
    Amplitude, Annotation, Channel, Coordinate, Label, Location, Proposal, Provenance,
    Recording, Reference, Relationship, ValidationError, Workspace,
)


@pytest.fixture
def recording():
    return Recording(sha256(b"synthetic ACQ review fixture").hexdigest(), "signal.ACQ", (
        Channel("fast", "Fast", "mV", 10., tuple(float(i) for i in range(21))),
        Channel("slow", "Slow", "%", 4., tuple(float(i + 90) for i in range(6))),
    ), source_size_bytes=28)


@pytest.fixture
def sidecar(tmp_path, recording):
    return sidecar_path(tmp_path / recording.name)


@pytest.fixture
def service(recording, sidecar):
    return AnnotationService(recording, JsonSidecarRepository(sidecar, recording), "independent reviewer")


def read_raw(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_raw(path, raw):
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


def persisted(service):
    return JsonSidecarRepository(service.repository.path, service.recording).load()


def generic_annotation(identity, location, *, code="event", label="Event"):
    return Annotation(identity, location, Label(code, label, {"custom": [1, "é", None]}),
                      Provenance("manual", "reviewer", "2026-10-02T17:00:00+00:00"),
                      {"custom_region_info": {"version": 1, "accepted": True}})


def test_first_open_creates_valid_empty_adjacent_json_and_only_that_file(recording, sidecar):
    assert not sidecar.exists()
    repository = JsonSidecarRepository(sidecar, recording)
    workspace = repository.load()
    assert workspace == Workspace(recording.id)
    assert sidecar.parent == sidecar_path(sidecar.parent / recording.name).parent
    assert list(sidecar.parent.iterdir()) == [sidecar]
    raw = read_raw(sidecar)
    assert raw["recording"]["id"] == recording.id
    assert raw["recording"]["file_size_bytes"] == 28
    assert raw["annotations"] == raw["relationships"] == raw["proposals"] == []
    assert from_dict(raw, recording) == workspace
    assert not any(k in raw for k in ("audit", "history", "revision", "snapshots"))
    assert all("values" not in c for c in raw["recording"]["channels"])


def test_reopen_loads_existing_bytes_without_rewriting(service):
    annotation = service.add("maximum", "fast", .36, None, "maximum")
    before = service.repository.path.read_bytes()
    reopened = AnnotationService(service.recording,
        JsonSidecarRepository(service.repository.path, service.recording), "other reviewer")
    assert reopened.workspace.annotations == [annotation]
    assert service.repository.path.read_bytes() == before


def test_create_edit_delete_autosave_only_current_state(service):
    first = service.add("maximum", "fast", .36, None, "old label")
    assert persisted(service).annotations == [first]
    updated = service.edit(first.id, "minimum", "slow", .36, None, "new label")
    assert (updated.start, updated.location.start.sample_index, updated.value) == (.25, 1, 91.)
    assert updated.id == first.id
    assert updated.provenance == first.provenance
    assert persisted(service).annotations == [updated]
    assert "old label" not in service.repository.path.read_text()
    service.delete([first.id])
    assert persisted(service).annotations == []
    final = service.repository.path.read_text()
    assert first.id not in final and "new label" not in final and "old label" not in final
    assert list(service.repository.path.parent.iterdir()) == [service.repository.path]
    assert not hasattr(service, "_undo") and not hasattr(service.workspace, "audit")


@pytest.mark.parametrize("payload", [
    b"{", b"", b"null", b"[]", b"\xff", b'{"schema_version":2,"schema_version":2}',
    b'{"metadata":{"x":NaN}}', b'{"metadata":{"x":Infinity}}',
])
def test_corrupt_json_refused_without_overwrite(recording, sidecar, payload):
    sidecar.write_bytes(payload)
    with pytest.raises(ValidationError):
        JsonSidecarRepository(sidecar, recording).load()
    assert sidecar.read_bytes() == payload
    assert list(sidecar.parent.iterdir()) == [sidecar]


@pytest.mark.parametrize("mutator", [
    lambda raw: raw.update(schema_version=999),
    lambda raw: raw.update(schema_version=True),
    lambda raw: raw.update(annotations={}),
    lambda raw: raw.update(proposals={}),
    lambda raw: raw.update(relationships={}),
    lambda raw: raw.update(metadata=[]),
    lambda raw: raw.update(extensions=[]),
    lambda raw: raw.update(history=[]),
    lambda raw: raw["recording"].update(id="different hash"),
    lambda raw: raw["recording"].update(file_size_bytes=999),
    lambda raw: raw["recording"]["channels"][0].update(sample_rate_hz=12.),
    lambda raw: raw["recording"]["channels"][0].update(sample_count=99),
    lambda raw: raw["recording"]["channels"][0].update(units="V"),
    lambda raw: raw["recording"]["channels"][0].update(name="another name"),
    lambda raw: raw["recording"]["channels"].reverse(),
])
def test_schema_and_acq_binding_are_validated_and_preserved(service, mutator):
    raw = read_raw(service.repository.path)
    mutator(raw)
    write_raw(service.repository.path, raw)
    before = service.repository.path.read_bytes()
    with pytest.raises(ValidationError):
        JsonSidecarRepository(service.repository.path, service.recording).load()
    assert service.repository.path.read_bytes() == before


def test_different_acq_cannot_reuse_existing_sidecar(service):
    other = replace(service.recording, id=sha256(b"other ACQ").hexdigest())
    before = service.repository.path.read_bytes()
    with pytest.raises(ValidationError):
        AnnotationService(other, JsonSidecarRepository(service.repository.path, other), "reviewer")
    assert service.repository.path.read_bytes() == before


@pytest.mark.parametrize("field,value", [("seconds", True), ("sample_index", True),
                                         ("sample_index", 1.5), ("sample_index", 19),
                                         ("seconds", .21)])
def test_point_sample_coordinates_validated_on_load(service, field, value):
    service.add("maximum", "fast", .2, None, "valid")
    raw = read_raw(service.repository.path)
    raw["annotations"][0]["location"]["start"][field] = value
    write_raw(service.repository.path, raw)
    before = service.repository.path.read_bytes()
    with pytest.raises(ValidationError):
        service.reload()
    assert service.repository.path.read_bytes() == before
    assert service.workspace.annotations[0].start == .2


@pytest.mark.parametrize("kind,channel,start,end", [
    ("maximum", "fast", math.nan, None), ("minimum", "fast", -.1, None),
    ("maximum", None, .2, None), ("interval", "slow", .2, 1.3),
    ("interval", "fast", .2, .2), ("interval", "", .2, .4),
])
def test_invalid_mutation_preserves_json_and_memory(service, kind, channel, start, end):
    service.add("maximum", "fast", .2, None, "existing")
    before = deepcopy(service.workspace)
    content = service.repository.path.read_bytes()
    with pytest.raises(ValidationError):
        service.add(kind, channel, start, end, "invalid")
    assert service.workspace == before
    assert service.repository.path.read_bytes() == content


def test_extensions_metadata_labels_and_future_geometries_survive_edit(service):
    point = service.add("maximum", "fast", .2, None, "point")
    candidate = deepcopy(service.workspace)
    candidate.annotations[0] = replace(point,
        label=Label("maximum", "point", {"quality": "reviewed", "score": .8}),
        extensions={"algorithm": {"version": "v3", "flags": [True, None]}})
    region = generic_annotation("region-1", Location("region", ("slow",),
        Coordinate(.25, 1), Coordinate(1., 4), Amplitude("%", minimum=89., maximum=99.)))
    whole = generic_annotation("recording-1", Location("recording"), code="recording_quality", label="Usable")
    interval = generic_annotation("interval-1", Location("interval", ("fast", "slow"),
        Coordinate(.13), Coordinate(1.2)), code="artifact", label="Motion")
    candidate.annotations.extend([region, whole, interval])
    candidate.metadata = {"study": {"protocol": "test", "notes": ["unicode é", None]}}
    candidate.extensions = {"future": {"nested": [1, {"value": False}]}}
    candidate.relationships = [Relationship("rel-1", Reference("annotation", point.id),
        Reference("channel", "slow"), Label("synchronizes", "Synchronizes", {"lag": .15}), {"custom": "retained"})]
    service.repository.save(candidate)
    service.reload()
    edited = service.edit(point.id, "minimum", "fast", .4, None, "changed")
    reopened = persisted(service)
    assert reopened == service.workspace
    assert reopened.annotations[1:] == [region, whole, interval]
    assert reopened.metadata == candidate.metadata
    assert reopened.extensions == candidate.extensions
    assert reopened.relationships == candidate.relationships
    assert edited.extensions == candidate.annotations[0].extensions
    assert edited.label.attributes == candidate.annotations[0].label.attributes
    assert edited.label.code == "minimum"
    assert edited.location.type == "point"


def test_relationship_endpoints_validate_and_protect_annotation_deletion(service):
    point = service.add("minimum", "fast", .2, None, "point")
    candidate = deepcopy(service.workspace)
    relation = Relationship("r1", Reference("annotation", point.id), Reference("channel", "slow"), Label("near", "near"))
    candidate.relationships.append(relation)
    service.repository.save(candidate)
    service.reload()
    before = service.repository.path.read_bytes()
    with pytest.raises(ValidationError, match="relationships"):
        service.delete([point.id])
    assert service.repository.path.read_bytes() == before
    for endpoint in (Reference("channel", "absent"), Reference("annotation", "absent"), Reference("proposal", point.id)):
        invalid = deepcopy(service.workspace)
        invalid.relationships[0] = replace(relation, target=endpoint)
        with pytest.raises(ValidationError):
            service.repository.save(invalid)
        assert service.repository.path.read_bytes() == before


def test_model_proposals_roundtrip_separately_and_acceptance_is_bound(service):
    loc = Location("point", ("fast",), Coordinate(.3, 3), amplitude=Amplitude("mV", value=3.))
    proposal = Proposal("proposal-1", loc, Label("artifact_peak", "Suggested event"), "future-model", "v2", .8,
        "2026-10-02T17:00:00+00:00", extensions={"input": "future"})
    service.register_proposals([proposal])
    assert persisted(service).annotations == []
    assert persisted(service).proposals == [proposal]
    service.review_proposal(proposal.id, True)
    reopened = persisted(service)
    annotation = reopened.annotations[0]
    assert annotation.location.type == "point" and annotation.label.code == "artifact_peak"
    assert annotation.provenance.proposal_id == proposal.id
    assert annotation.origin == "model_reviewed"
    assert reopened.proposals[0].status == "accepted"
    bad = deepcopy(reopened)
    bad.annotations[0] = replace(annotation, label=Label("other", "Mismatched"))
    with pytest.raises(ValidationError):
        service.repository.save(bad)


def test_stale_writer_cannot_erase_current_state_and_can_recover_by_reload(service):
    other = AnnotationService(service.recording,
        JsonSidecarRepository(service.repository.path, service.recording), "second")
    first = service.add("maximum", "fast", .2, None, "first")
    before = service.repository.path.read_bytes()
    with pytest.raises(ConflictError):
        other.add("minimum", "fast", .3, None, "second")
    assert other.workspace.annotations == []
    assert service.repository.path.read_bytes() == before
    other.reload()
    second = other.add("minimum", "fast", .3, None, "second")
    assert persisted(other).annotations == [first, second]


def test_external_corruption_after_open_cannot_be_overwritten(service):
    before_workspace = deepcopy(service.workspace)
    service.repository.path.write_bytes(b"{broken external edit")
    with pytest.raises(ValidationError):
        service.add("maximum", "fast", .2, None, "not saved")
    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == b"{broken external edit"


def test_externally_deleted_sidecar_is_not_silently_recreated_by_save(service):
    service.repository.path.unlink()
    with pytest.raises(StorageError):
        service.add("maximum", "fast", .2, None, "not saved")
    assert service.workspace.annotations == []
    assert not service.repository.path.exists()


@pytest.mark.parametrize("failure_target", ["mkstemp", "fsync", "replace"])
def test_autosave_os_failures_preserve_previous_bytes_and_memory(service, monkeypatch, failure_target):
    service.add("maximum", "fast", .2, None, "original")
    before_workspace = deepcopy(service.workspace)
    before_bytes = service.repository.path.read_bytes()
    def denied(*args, **kwargs):
        raise PermissionError("injected write denial")
    monkeypatch.setattr(storage.tempfile if failure_target == "mkstemp" else storage.os, failure_target, denied)
    with pytest.raises(StorageError, match="permissions"):
        service.edit(service.workspace.annotations[0].id, "minimum", "fast", .4, None, "not saved")
    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == before_bytes
    assert list(service.repository.path.parent.iterdir()) == [service.repository.path]


def test_first_open_write_failure_reports_actionable_error_and_leaves_no_sidecar(recording, sidecar, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("injected first-open denial")
    monkeypatch.setattr(storage.tempfile, "mkstemp", denied)
    with pytest.raises(StorageError, match="permissions"):
        JsonSidecarRepository(sidecar, recording).load()
    assert not sidecar.exists()
    assert not list(sidecar.parent.iterdir())


def test_existing_lock_is_reported_and_never_removed(service):
    lock = service.repository.path.with_name(service.repository.path.name + ".lock")
    lock.mkdir()
    before = service.repository.path.read_bytes()
    try:
        with pytest.raises(ConflictError, match="lock"):
            service.add("maximum", "fast", .2, None, "blocked")
        assert lock.exists()
        assert service.repository.path.read_bytes() == before
    finally:
        lock.rmdir()


def test_symlink_sidecar_is_rejected_without_touching_target(recording, sidecar, tmp_path):
    target = tmp_path / "valuable.json"
    target.write_text("do not touch", encoding="utf-8")
    sidecar.symlink_to(target)
    with pytest.raises(StorageError, match="symbolic"):
        JsonSidecarRepository(sidecar, recording).load()
    assert sidecar.is_symlink()
    assert target.read_text() == "do not touch"


def test_direct_save_without_loading_cannot_replace_existing_state(service):
    before = service.repository.path.read_bytes()
    repository = JsonSidecarRepository(service.repository.path, service.recording)
    with pytest.raises(ConflictError):
        repository.save(Workspace(service.recording.id))
    assert service.repository.path.read_bytes() == before


def test_failed_lock_cleanup_never_leaves_disk_newer_than_service_memory(service, monkeypatch):
    """A post-commit cleanup failure must not be reported as an unsaved mutation."""
    original_rmdir = Path.rmdir
    lock = service.repository.path.with_name(service.repository.path.name + ".lock")
    def fail_only_lock(path):
        if path == lock:
            raise PermissionError("injected lock cleanup failure")
        return original_rmdir(path)
    with monkeypatch.context() as patch, pytest.warns(RuntimeWarning, match="lock"):
        patch.setattr(Path, "rmdir", fail_only_lock)
        try:
            service.add("maximum", "fast", .2, None, "committed mutation")
        except OSError:
            pass
        assert from_dict(read_raw(service.repository.path), service.recording) == service.workspace
    if lock.exists():
        lock.rmdir()


def test_corrupt_huge_integer_coordinate_is_a_validation_error(service):
    service.add("maximum", "fast", .2, None, "valid")
    raw = read_raw(service.repository.path)
    raw["annotations"][0]["location"]["start"]["seconds"] = 10 ** 400
    write_raw(service.repository.path, raw)
    before = service.repository.path.read_bytes()
    with pytest.raises(ValidationError):
        service.reload()
    assert service.repository.path.read_bytes() == before


def test_free_time_amplitude_point_is_valid_future_geometry(service):
    candidate = deepcopy(service.workspace)
    free_point = generic_annotation("free-point", Location("point", ("fast",),
        Coordinate(.2137), amplitude=Amplitude("mV", value=-2.75)), code="custom_event")
    candidate.annotations.append(free_point)
    service.repository.save(candidate)
    service.reload()
    assert persisted(service).annotations == [free_point]
    assert service.workspace.annotations[0].location.start.sample_index is None
