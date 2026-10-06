"""Framework-independent application coverage for the shared root catalog."""
from copy import deepcopy
from hashlib import sha256

import pytest

from signal_annotation.adapters import storage
from signal_annotation.adapters.catalog_storage import CatalogConflictError, JsonLabelCatalogRepository
from signal_annotation.adapters.storage import JsonSidecarRepository, StorageError, sidecar_path
from signal_annotation.application import AnnotationService, interval_color, point_colors
from signal_annotation.domain import Channel, Label, Recording, ValidationError
from signal_annotation.label_catalog import LabelCatalog
from signal_annotation.validation import (
    AMPLITUDE_LABEL_COLOR_ATTRIBUTE, AMPLITUDE_LABEL_ID_ATTRIBUTE,
    AMPLITUDE_LABEL_NAME_ATTRIBUTE, INTERVAL_LABEL_COLOR_ATTRIBUTE,
    INTERVAL_LABEL_ID_ATTRIBUTE, INTERVAL_LABEL_NAME_ATTRIBUTE,
)


@pytest.fixture
def recording():
    return Recording(sha256(b"catalog application fixture").hexdigest(), "catalog.ACQ", (
        Channel("fast", "Fast", "mV", 10., tuple(float(index) for index in range(31))),
    ), source_size_bytes=31)


@pytest.fixture
def repositories(tmp_path, recording):
    return (JsonSidecarRepository(sidecar_path(tmp_path / recording.name), recording),
            JsonLabelCatalogRepository(tmp_path))


@pytest.fixture
def service(recording, repositories):
    sidecar, catalog = repositories
    return AnnotationService(recording, sidecar, "catalog tester", catalog)


def both_bytes(service):
    catalog_path = service.catalog_repository.path
    return (service.repository.path.read_bytes(),
            catalog_path.read_bytes() if catalog_path.exists() else None)


def test_open_and_reload_do_not_create_an_absent_root_catalog(service):
    assert not service.catalog_repository.path.exists()
    service.reload()
    assert service.catalog == LabelCatalog()
    assert not service.catalog_repository.path.exists()


def test_corrupt_catalog_blocks_open_before_an_absent_sidecar_is_created(recording, tmp_path):
    catalog = JsonLabelCatalogRepository(tmp_path)
    catalog.path.write_bytes(b"{broken")
    sidecar = JsonSidecarRepository(sidecar_path(tmp_path / recording.name), recording)
    before = catalog.path.read_bytes()

    with pytest.raises(ValidationError, match="Corrupt label catalog JSON"):
        AnnotationService(recording, sidecar, "catalog tester", catalog)

    assert not sidecar.path.exists()
    assert catalog.path.read_bytes() == before


def test_external_entries_store_self_contained_snapshots_and_new_sidecar_reopens(
        service, recording):
    amplitude = service.add_amplitude_label("Maximum")
    interval = service.add_interval_label("Artifact")
    marker = service.add_catalog_marker("fast", .21, amplitude["id"])
    band = service.add_catalog_interval(None, .4, 1.2, interval["id"])
    generic = service.add("marker", "fast", .3, None, "Generic")
    reassigned = service.reassign_point_label(generic.id, amplitude["id"])

    assert marker.label == Label("marker", "Maximum", {
        AMPLITUDE_LABEL_ID_ATTRIBUTE: amplitude["id"],
        AMPLITUDE_LABEL_NAME_ATTRIBUTE: "Maximum",
        AMPLITUDE_LABEL_COLOR_ATTRIBUTE: amplitude["color"],
    })
    assert band.label == Label("interval", "Artifact", {
        INTERVAL_LABEL_ID_ATTRIBUTE: interval["id"],
        INTERVAL_LABEL_NAME_ATTRIBUTE: "Artifact",
        INTERVAL_LABEL_COLOR_ATTRIBUTE: interval["color"],
    })
    assert reassigned == marker.__class__(
        generic.id, generic.location, marker.label, generic.provenance, generic.extensions)
    assert service.workspace.metadata == {}
    assert point_colors([marker])[marker.id] == amplitude["color"]
    assert interval_color(band, service.workspace) == interval["color"]

    # A sidecar with ID/name/color snapshots remains valid even without the root file.
    reopened = AnnotationService(recording,
        JsonSidecarRepository(service.repository.path, recording), "reopened")
    assert reopened.workspace.annotations == [marker, band, reassigned]

    service.reload()
    assert service.workspace.annotations == [marker, band, reassigned]
    assert service.catalog == service.catalog_repository.load()


def test_legacy_three_argument_service_remains_metadata_catalog_compatible(
        recording, repositories):
    sidecar, catalog = repositories
    legacy = AnnotationService(recording, sidecar, "legacy")
    entry = legacy.add_amplitude_label("Legacy maximum")
    marker = legacy.add_catalog_marker("fast", .2, entry["name"])

    assert "id" not in entry
    assert AMPLITUDE_LABEL_ID_ATTRIBUTE not in marker.label.attributes
    assert not catalog.path.exists()
    reopened = AnnotationService(recording,
        JsonSidecarRepository(sidecar.path, recording), "legacy reopen")
    assert reopened.workspace.annotations == [marker]


def test_catalog_add_refreshes_from_committed_durable_content_without_a_second_write(
        service):
    # The shared repository can advance while this service's cache remains stale.
    outside = service.catalog_repository.add("interval_labels", "Outside")
    assert service.catalog == LabelCatalog()
    before = service.catalog_repository.path.read_bytes()

    added = service.add_interval_label("Service")

    assert service.catalog.interval_labels == tuple(service.catalog_repository.load().interval_labels)
    assert [entry.id for entry in service.catalog.interval_labels] == [outside.id, added["id"]]
    assert service.catalog.interval_labels[1].color == added["color"]
    assert service.catalog_repository.path.read_bytes() != before


def test_external_caps_are_independent_and_entries_support_unlimited_instances(service):
    amplitudes = [service.add_amplitude_label(f"Amplitude {index}") for index in range(10)]
    intervals = [service.add_interval_label(f"Interval {index}") for index in range(10)]
    markers = [service.add_catalog_marker("fast", .1 * (index % 10), amplitudes[0]["id"])
               for index in range(12)]
    bands = [service.add_catalog_interval("fast", .1, .2, intervals[0]["id"])
             for _ in range(12)]

    assert {item.label.attributes[AMPLITUDE_LABEL_ID_ATTRIBUTE] for item in markers} == {amplitudes[0]["id"]}
    assert {item.label.attributes[INTERVAL_LABEL_ID_ATTRIBUTE] for item in bands} == {intervals[0]["id"]}
    before = both_bytes(service)
    with pytest.raises(ValidationError, match="At most 10 labels"):
        service.add_amplitude_label("Too many")
    with pytest.raises(ValidationError, match="At most 10 labels"):
        service.add_interval_label("Too many")
    assert both_bytes(service) == before


def test_current_durable_catalog_is_used_when_the_service_cache_is_stale(service, tmp_path):
    first = service.add_amplitude_label("First")
    other = JsonLabelCatalogRepository(tmp_path)
    second = other.add("amplitude_labels", "Second")
    assert [entry.id for entry in service.catalog.amplitude_labels] == [first["id"]]

    marker = service.add_catalog_marker("fast", .2, second.id)

    assert marker.label.attributes[AMPLITUDE_LABEL_ID_ATTRIBUTE] == second.id
    assert [entry.id for entry in service.catalog.amplitude_labels] == [first["id"], second.id]


def test_stale_external_catalog_add_conflict_preserves_both_files_and_service_state(
        service, tmp_path):
    before_workspace = deepcopy(service.workspace)
    other = JsonLabelCatalogRepository(tmp_path)
    other.add("amplitude_labels", "Written elsewhere")
    before = both_bytes(service)
    before_catalog = service.catalog

    with pytest.raises(CatalogConflictError, match="changed in another program"):
        service.add_amplitude_label("Stale add")

    assert service.workspace == before_workspace
    assert service.catalog == before_catalog
    assert both_bytes(service) == before


def test_absent_corrupt_or_replaced_catalog_rejects_commands_without_sidecar_writes(service):
    entry = service.add_amplitude_label("Registered")
    before = both_bytes(service)
    service.catalog_repository.path.unlink()
    absent = both_bytes(service)
    with pytest.raises(ValidationError, match="Choose a registered amplitude label"):
        service.add_catalog_marker("fast", .2, entry["id"])
    assert both_bytes(service) == absent

    service.catalog_repository.path.write_bytes(b"{broken")
    corrupt = both_bytes(service)
    with pytest.raises(ValidationError, match="Corrupt label catalog JSON"):
        service.add_catalog_marker("fast", .2, entry["id"])
    assert both_bytes(service) == corrupt
    assert before[0] == absent[0] == corrupt[0]


def test_invalid_annotation_and_sidecar_save_failure_preserve_both_files(service, monkeypatch):
    entry = service.add_amplitude_label("Registered")
    before_workspace = deepcopy(service.workspace)
    before = both_bytes(service)

    with pytest.raises(ValidationError, match="Unknown channel"):
        service.add_catalog_marker("missing", .2, entry["id"])
    assert service.workspace == before_workspace
    assert both_bytes(service) == before

    def denied(*args, **kwargs):
        raise PermissionError("injected sidecar failure")

    monkeypatch.setattr(storage.os, "replace", denied)
    with pytest.raises(StorageError, match="injected sidecar failure"):
        service.add_catalog_marker("fast", .2, entry["id"])
    assert service.workspace == before_workspace
    assert both_bytes(service) == before


def test_snapshot_validation_does_not_require_legacy_metadata_catalog(service):
    entry = service.add_amplitude_label("Snapshot")
    marker = service.add_catalog_marker("fast", .2, entry["id"])
    candidate = deepcopy(service.workspace)
    candidate.annotations[0] = marker.__class__(
        marker.id, marker.location,
        Label("marker", marker.label.text, {**marker.label.attributes,
                                              AMPLITUDE_LABEL_COLOR_ATTRIBUTE: "#bad"}),
        marker.provenance, marker.extensions)
    before = both_bytes(service)

    with pytest.raises(ValidationError, match="predefined color"):
        service._commit(candidate)
    assert both_bytes(service) == before
