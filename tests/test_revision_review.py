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
from signal_annotation.application import (AnnotationService, LEGACY_INTERVAL_COLOR,
                                           interval_color, point_colors)
from signal_annotation.domain import (
    Amplitude, Annotation, Channel, Coordinate, Label, Location, Proposal, Provenance,
    Recording, Reference, Relationship, ValidationError, Workspace,
)
from signal_annotation.validation import (
    AMPLITUDE_LABEL_CATALOG_KEY, AMPLITUDE_LABEL_COLOR_ATTRIBUTE,
    AMPLITUDE_LABEL_COLORS, AMPLITUDE_LABEL_NAME_ATTRIBUTE, GENERAL_NOTES_KEY,
    INTERVAL_LABEL_CATALOG_KEY, INTERVAL_LABEL_COLOR_ATTRIBUTE, INTERVAL_LABEL_COLORS,
    INTERVAL_LABEL_NAME_ATTRIBUTE,
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


def test_move_point_snaps_and_preserves_annotation_fields(service):
    point = service.add("maximum", "fast", .16, None, "keep label")
    other = service.add("minimum", "slow", .3, None, "untouched")
    candidate = deepcopy(service.workspace)
    candidate.annotations[0] = replace(point,
        label=replace(point.label, attributes={"keep": [1, "value"]}),
        extensions={"keep": {"nested": True}})
    service.repository.save(candidate)
    service.reload()
    before = service.workspace.annotations[0]

    moved = service.move_point(before.id, .26)

    assert (moved.start, moved.location.start.sample_index, moved.value) == (.3, 3, 3.)
    assert moved.id == before.id
    assert moved.label == before.label
    assert moved.provenance == before.provenance
    assert moved.extensions == before.extensions
    assert service.workspace.annotations[1] == other
    assert persisted(service).annotations == [moved, other]


def test_resize_interval_updates_only_one_boundary_and_reloads(service):
    interval = service.add("interval", "fast", .2, 1., "keep interval")
    other = service.add("minimum", "slow", .3, None, "untouched")
    candidate = deepcopy(service.workspace)
    candidate.annotations[0] = replace(interval,
        location=replace(interval.location, start=Coordinate(.2, 2), end=Coordinate(1., 10)),
        label=replace(interval.label, attributes={"keep": [1, "value"]}),
        extensions={"keep": {"nested": True}})
    service.repository.save(candidate)
    service.reload()
    before = service.workspace.annotations[0]

    resized = service.resize_interval(before.id, "start", .35)

    expected = replace(before, location=replace(before.location, start=Coordinate(.35)))
    assert resized == expected
    assert resized.location.start.sample_index is None
    assert resized.location.end == Coordinate(1., 10)
    assert resized.id == before.id
    assert resized.label == before.label
    assert resized.provenance == before.provenance
    assert resized.extensions == before.extensions
    assert service.workspace.annotations == [expected, other]
    assert persisted(service).annotations == [expected, other]


@pytest.mark.parametrize("channel,endpoint,seconds", [
    ("slow", "start", 1.), ("slow", "end", .25),
    ("slow", "end", 1.3), ("slow", "start", -.01),
    ("slow", "end", float("nan")), ("slow", "end", True),
    (None, "end", 2.01), (None, "middle", .5),
])
def test_invalid_interval_resize_respects_order_and_channel_or_recording_bounds(
        service, channel, endpoint, seconds):
    interval = service.add("interval", channel, .25, 1., "interval")
    before_workspace = deepcopy(service.workspace)
    before_bytes = service.repository.path.read_bytes()

    with pytest.raises(ValidationError):
        service.resize_interval(interval.id, endpoint, seconds)

    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == before_bytes


def test_interval_resize_rejects_unknown_point_and_model_reviewed_targets(service):
    point = service.add("maximum", "fast", .2, None, "point")
    proposal = Proposal("interval-proposal", Location("interval", ("fast",),
        Coordinate(.3), Coordinate(.8)), Label("artifact", "Suggested interval"),
        "future-model", "v1", .8, "2026-10-04T00:00:00+00:00")
    service.register_proposals([proposal])
    service.review_proposal(proposal.id, True)
    reviewed = service.workspace.annotations[1]
    before_workspace = deepcopy(service.workspace)
    before_bytes = service.repository.path.read_bytes()

    for identity in ("missing", point.id, reviewed.id):
        with pytest.raises(ValidationError):
            service.resize_interval(identity, "start", .4)
        assert service.workspace == before_workspace
        assert service.repository.path.read_bytes() == before_bytes


@pytest.mark.parametrize("position", [-.01, 2.01, float("nan"), True])
def test_invalid_point_move_changes_neither_memory_nor_disk(service, position):
    point = service.add("maximum", "fast", .2, None, "point")
    before_workspace = deepcopy(service.workspace)
    before_bytes = service.repository.path.read_bytes()

    with pytest.raises(ValidationError):
        service.move_point(point.id, position)

    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == before_bytes


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


def test_amplitude_label_catalog_preserves_metadata_has_ten_entry_limit_and_unlimited_reuse(service):
    candidate = deepcopy(service.workspace)
    candidate.metadata = {"study": {"protocol": "keep", "nested": [1, None]}}
    service.repository.save(candidate)
    service.reload()

    entries = [service.add_amplitude_label(f"Amplitude type {index}") for index in range(10)]
    assert [entry["color"] for entry in entries] == list(AMPLITUDE_LABEL_COLORS)
    assert service.workspace.metadata["study"] == candidate.metadata["study"]
    assert persisted(service).metadata[AMPLITUDE_LABEL_CATALOG_KEY] == entries

    points = [service.add_catalog_marker("fast", .1, entries[0]["name"])
              for _ in range(12)]
    assert len(points) == 12
    assert all(point.label == Label("marker", entries[0]["name"], {
        AMPLITUDE_LABEL_NAME_ATTRIBUTE: entries[0]["name"],
        AMPLITUDE_LABEL_COLOR_ATTRIBUTE: entries[0]["color"],
    }) for point in points)
    assert set(point_colors(service.workspace.annotations).values()) == {entries[0]["color"]}

    interval_entry = service.add_interval_label("Independent interval")
    assert interval_entry["color"] == INTERVAL_LABEL_COLORS[0]
    before_workspace = deepcopy(service.workspace)
    before_bytes = service.repository.path.read_bytes()
    with pytest.raises(ValidationError, match="At most 10 amplitude labels"):
        service.add_amplitude_label("Eleventh type")
    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == before_bytes

    reopened = AnnotationService(service.recording,
        JsonSidecarRepository(service.repository.path, service.recording), "other reviewer")
    assert reopened.workspace.metadata[AMPLITUDE_LABEL_CATALOG_KEY] == entries
    assert len(reopened.workspace.annotations) == 12
    assert set(point_colors(reopened.workspace.annotations).values()) == {entries[0]["color"]}


@pytest.mark.parametrize("selection", ["", "Missing", None, 7])
def test_invalid_amplitude_catalog_selection_does_not_mutate_memory_or_disk(service, selection):
    service.add_amplitude_label("Registered")
    before_workspace = deepcopy(service.workspace)
    before_bytes = service.repository.path.read_bytes()

    with pytest.raises(ValidationError, match="Choose a registered amplitude label"):
        service.add_catalog_marker("fast", .1, selection)

    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == before_bytes


def test_reassign_generic_point_uses_existing_catalog_entry_and_preserves_other_fields(service):
    first = service.add_amplitude_label("Maximum")
    second = service.add_amplitude_label("Minimum")
    point = service.add_catalog_marker("fast", .2, first["name"])
    candidate = deepcopy(service.workspace)
    candidate.annotations[0] = replace(point,
        label=replace(point.label, attributes={**point.label.attributes, "project:quality": "kept"}),
        extensions={"project:note": {"nested": True}})
    service.repository.save(candidate)
    service.reload()
    before = service.workspace.annotations[0]

    reassigned = service.reassign_point_label(before.id, second["name"])

    expected_attributes = {
        "project:quality": "kept",
        AMPLITUDE_LABEL_NAME_ATTRIBUTE: second["name"],
        AMPLITUDE_LABEL_COLOR_ATTRIBUTE: second["color"],
    }
    assert reassigned == replace(before,
        label=replace(before.label, text=second["name"], attributes=expected_attributes))
    assert service.workspace.annotations == [reassigned]
    assert persisted(service).annotations == [reassigned]


def test_invalid_point_reassignment_and_broken_semantic_link_roll_back(service):
    entry = service.add_amplitude_label("Maximum")
    point = service.add_catalog_marker("fast", .2, entry["name"])
    before_workspace = deepcopy(service.workspace)
    before_bytes = service.repository.path.read_bytes()

    with pytest.raises(ValidationError, match="Choose a registered amplitude label"):
        service.reassign_point_label(point.id, "Missing")
    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == before_bytes

    candidate = deepcopy(service.workspace)
    candidate.annotations[0] = replace(point, label=replace(point.label, text="Detached"))
    with pytest.raises(ValidationError, match="does not match its catalog entry"):
        service._commit(candidate)
    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == before_bytes


@pytest.mark.parametrize("catalog", [
    {},
    [{"name": "", "color": AMPLITUDE_LABEL_COLORS[0]}],
    [{"name": " Padded", "color": AMPLITUDE_LABEL_COLORS[0]}],
    [{"name": "One", "color": "#000000"}],
    [{"name": "One", "color": AMPLITUDE_LABEL_COLORS[0], "extra": True}],
    [{"name": "One", "color": AMPLITUDE_LABEL_COLORS[0]},
     {"name": "one", "color": AMPLITUDE_LABEL_COLORS[1]}],
    [{"name": "One", "color": AMPLITUDE_LABEL_COLORS[0]},
     {"name": "Two", "color": AMPLITUDE_LABEL_COLORS[0]}],
    [{"name": f"Type {index}", "color": AMPLITUDE_LABEL_COLORS[index % 10]}
     for index in range(11)],
])
def test_malformed_amplitude_catalog_is_rejected_without_overwrite(service, catalog):
    raw = read_raw(service.repository.path)
    raw["metadata"][AMPLITUDE_LABEL_CATALOG_KEY] = catalog
    write_raw(service.repository.path, raw)
    before_bytes = service.repository.path.read_bytes()
    before_workspace = deepcopy(service.workspace)

    with pytest.raises(ValidationError):
        service.reload()

    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == before_bytes


def test_legacy_unlinked_points_have_no_instance_cap_and_render_deterministically(service):
    points = [service.add("marker", "fast", .1, None, f"Legacy {index}")
              for index in range(12)]
    maximum = service.add("maximum", "fast", .2, None, "Legacy maximum")
    minimum = service.add("minimum", "fast", .3, None, "Legacy minimum")
    assert AMPLITUDE_LABEL_CATALOG_KEY not in service.workspace.metadata
    assert all(AMPLITUDE_LABEL_NAME_ATTRIBUTE not in point.label.attributes for point in points)
    expected = point_colors(service.workspace.annotations)

    reopened = AnnotationService(service.recording,
        JsonSidecarRepository(service.repository.path, service.recording), "other reviewer")
    assert reopened.workspace.annotations[-2:] == [maximum, minimum]
    assert point_colors(reopened.workspace.annotations) == expected


def test_interval_label_catalog_preserves_metadata_persists_and_has_independent_limit(service):
    candidate = deepcopy(service.workspace)
    candidate.metadata = {"study": {"protocol": "keep", "nested": [1, None]}}
    service.repository.save(candidate)
    service.reload()

    entries = [service.add_interval_label(f"Interval type {index}") for index in range(10)]
    assert [entry["color"] for entry in entries] == list(INTERVAL_LABEL_COLORS)
    assert service.workspace.metadata["study"] == candidate.metadata["study"]
    assert persisted(service).metadata[INTERVAL_LABEL_CATALOG_KEY] == entries

    intervals = [service.add_catalog_interval("fast", .1, .2, entries[0]["name"])
                 for _ in range(12)]
    assert len(intervals) == 12
    assert all(annotation.label.text == entries[0]["name"] for annotation in intervals)
    before_workspace = deepcopy(service.workspace)
    before_bytes = service.repository.path.read_bytes()

    with pytest.raises(ValidationError, match="At most 10 interval labels"):
        service.add_interval_label("Eleventh type")

    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == before_bytes


def test_general_notes_preserve_sibling_metadata_annotations_and_reload(service):
    annotation = service.add("maximum", "fast", .2, None, "kept")
    candidate = deepcopy(service.workspace)
    candidate.metadata = {"study": {"protocol": "keep", "values": [1, None]}}
    service.repository.save(candidate)
    service.reload()
    notes = "First observation\nSecond observation\n  Exact free text"

    service.save_general_notes(notes)

    assert service.workspace.metadata == {"study": candidate.metadata["study"], GENERAL_NOTES_KEY: notes}
    raw = read_raw(service.repository.path)
    assert raw["metadata"] == service.workspace.metadata
    reopened = AnnotationService(service.recording,
        JsonSidecarRepository(service.repository.path, service.recording), "other reviewer")
    assert reopened.workspace.metadata == service.workspace.metadata
    assert reopened.workspace.annotations == [annotation]

    reopened.save_general_notes("")
    assert persisted(reopened).metadata == {"study": candidate.metadata["study"], GENERAL_NOTES_KEY: ""}


@pytest.mark.parametrize("notes", [None, [], {"text": "invalid"}, 1])
def test_invalid_general_notes_do_not_mutate_memory_or_disk(service, notes):
    before_workspace = deepcopy(service.workspace)
    before_bytes = service.repository.path.read_bytes()

    with pytest.raises(ValidationError, match="General recording notes must be a string"):
        service.save_general_notes(notes)

    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == before_bytes


@pytest.mark.parametrize("notes", [None, [], {"text": "invalid"}, 1])
def test_malformed_existing_general_notes_are_rejected_without_overwrite(service, notes):
    raw = read_raw(service.repository.path)
    raw["metadata"][GENERAL_NOTES_KEY] = notes
    write_raw(service.repository.path, raw)
    before_bytes = service.repository.path.read_bytes()
    before_workspace = deepcopy(service.workspace)

    with pytest.raises(ValidationError, match="General recording notes must be a string"):
        service.reload()

    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == before_bytes


def test_catalog_interval_selection_persists_semantic_link_color_and_reload(service):
    entry = service.add_interval_label("Motion artifact")
    annotation = service.add_catalog_interval(None, .25, 1., entry["name"])
    expected_attributes = {
        INTERVAL_LABEL_NAME_ATTRIBUTE: entry["name"],
        INTERVAL_LABEL_COLOR_ATTRIBUTE: entry["color"],
    }

    assert annotation.label == Label("interval", entry["name"], expected_attributes)
    raw = read_raw(service.repository.path)
    assert raw["metadata"][INTERVAL_LABEL_CATALOG_KEY] == [entry]
    assert raw["annotations"][0]["label"] == {
        "code": "interval", "text": entry["name"], "attributes": expected_attributes,
    }
    reopened = AnnotationService(service.recording,
        JsonSidecarRepository(service.repository.path, service.recording), "other reviewer")
    assert reopened.workspace.annotations == [annotation]
    assert interval_color(reopened.workspace.annotations[0], reopened.workspace) == entry["color"]

    resized = reopened.resize_interval(annotation.id, "start", .3)
    assert resized.label == annotation.label
    assert persisted(reopened).annotations == [resized]


@pytest.mark.parametrize("name", ["", "   ", "Motion", " motion "])
def test_blank_and_duplicate_interval_label_names_do_not_mutate_memory_or_disk(service, name):
    service.add_interval_label("Motion")
    before_workspace = deepcopy(service.workspace)
    before_bytes = service.repository.path.read_bytes()

    with pytest.raises(ValidationError):
        service.add_interval_label(name)

    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == before_bytes


@pytest.mark.parametrize("selection", ["", "Missing", None, 7])
def test_invalid_interval_catalog_selection_does_not_mutate_memory_or_disk(service, selection):
    service.add_interval_label("Registered")
    before_workspace = deepcopy(service.workspace)
    before_bytes = service.repository.path.read_bytes()

    with pytest.raises(ValidationError, match="Choose a registered interval label"):
        service.add_catalog_interval("fast", .1, .2, selection)

    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == before_bytes


@pytest.mark.parametrize("catalog", [
    {},
    [{"name": "", "color": INTERVAL_LABEL_COLORS[0]}],
    [{"name": " Padded", "color": INTERVAL_LABEL_COLORS[0]}],
    [{"name": "One", "color": "#000000"}],
    [{"name": "One", "color": INTERVAL_LABEL_COLORS[0], "extra": True}],
    [{"name": "One", "color": INTERVAL_LABEL_COLORS[0]},
     {"name": "one", "color": INTERVAL_LABEL_COLORS[1]}],
    [{"name": "One", "color": INTERVAL_LABEL_COLORS[0]},
     {"name": "Two", "color": INTERVAL_LABEL_COLORS[0]}],
    [{"name": f"Type {index}", "color": INTERVAL_LABEL_COLORS[index % 10]}
     for index in range(11)],
])
def test_malformed_interval_catalog_is_rejected_without_overwrite(service, catalog):
    raw = read_raw(service.repository.path)
    raw["metadata"][INTERVAL_LABEL_CATALOG_KEY] = catalog
    write_raw(service.repository.path, raw)
    before_bytes = service.repository.path.read_bytes()
    before_workspace = deepcopy(service.workspace)

    with pytest.raises(ValidationError):
        service.reload()

    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == before_bytes


def test_legacy_interval_without_catalog_or_color_remains_valid_with_stable_fallback(service):
    legacy = service.add("interval", "fast", .1, .2, "Legacy interval")
    assert INTERVAL_LABEL_CATALOG_KEY not in service.workspace.metadata
    assert legacy.label.attributes == {}
    assert interval_color(legacy, service.workspace) == LEGACY_INTERVAL_COLOR

    reopened = persisted(service)
    assert reopened.annotations == [legacy]
    assert interval_color(reopened.annotations[0], reopened) == LEGACY_INTERVAL_COLOR
