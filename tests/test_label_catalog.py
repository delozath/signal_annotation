import json

import pytest

from signal_annotation.adapters import catalog_storage
from signal_annotation.adapters.catalog_storage import CatalogConflictError, CatalogStorageError, JsonLabelCatalogRepository
from signal_annotation.domain import ValidationError
from signal_annotation.label_catalog import (AMPLITUDE_LABEL_COLORS, INTERVAL_LABEL_COLORS,
                                             LabelCatalog, catalog_from_dict, catalog_to_dict)


def test_absent_load_is_empty_and_never_creates_real_or_temporary_catalog(tmp_path):
    repository = JsonLabelCatalogRepository(tmp_path)
    assert repository.load() == LabelCatalog()
    assert not repository.path.exists()
    assert list(tmp_path.iterdir()) == []


def test_first_add_creates_valid_catalog_with_trimmed_stable_id_and_reload(tmp_path):
    repository = JsonLabelCatalogRepository(tmp_path)
    entry = repository.add("amplitude_labels", "  Maximum  ")
    assert entry.name == "Maximum" and entry.id
    raw = json.loads(repository.path.read_text(encoding="utf-8"))
    assert raw == {"schema_version": 1, "amplitude_labels": [entry.__dict__], "interval_labels": []}
    assert JsonLabelCatalogRepository(tmp_path).load().amplitude_labels == (entry,)


def test_collections_have_independent_caps_colors_and_casefolded_names(tmp_path):
    repository = JsonLabelCatalogRepository(tmp_path)
    repository.add("interval_labels", "Artifact")
    before_duplicate = repository.path.read_bytes()
    with pytest.raises(ValidationError):
        repository.add("interval_labels", " artifact ")
    assert repository.path.read_bytes() == before_duplicate
    amplitude = [repository.add("amplitude_labels", f"A {i}") for i in range(10)]
    interval = [JsonLabelCatalogRepository(tmp_path).load().interval_labels[0]]
    interval.extend(repository.add("interval_labels", f"I {i}") for i in range(9))
    assert [entry.color for entry in amplitude] == list(AMPLITUDE_LABEL_COLORS)
    assert [entry.color for entry in interval] == list(INTERVAL_LABEL_COLORS)
    before = repository.path.read_bytes()
    with pytest.raises(ValidationError):
        repository.add("amplitude_labels", "one more")
    assert repository.path.read_bytes() == before


@pytest.mark.parametrize("raw", [
    {"schema_version": 1, "amplitude_labels": [], "interval_labels": [], "unknown": True},
    {"schema_version": 2, "amplitude_labels": [], "interval_labels": []},
    {"schema_version": 1, "amplitude_labels": [{"id": "x", "name": "One", "color": "#bad"}], "interval_labels": []},
    {"schema_version": 1, "amplitude_labels": [{"id": "x", "name": "One", "color": AMPLITUDE_LABEL_COLORS[0]}, {"id": "x", "name": "Two", "color": AMPLITUDE_LABEL_COLORS[1]}], "interval_labels": []},
])
def test_unknown_malformed_and_duplicate_catalog_content_is_rejected_without_overwrite(tmp_path, raw):
    path = tmp_path / "label_catalog.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValidationError):
        JsonLabelCatalogRepository(tmp_path).load()
    assert path.read_bytes() == before


def test_corruption_conflict_and_deleted_file_are_not_overwritten(tmp_path):
    first = JsonLabelCatalogRepository(tmp_path)
    first.load()
    stale = JsonLabelCatalogRepository(tmp_path)
    stale.load()
    first.add("amplitude_labels", "First")
    before = first.path.read_bytes()
    with pytest.raises(CatalogConflictError):
        stale.add("amplitude_labels", "Second")
    assert first.path.read_bytes() == before
    first.path.write_bytes(b"{broken")
    with pytest.raises(ValidationError):
        first.add("amplitude_labels", "Third")
    assert first.path.read_bytes() == b"{broken"


@pytest.mark.parametrize("failure_target", ["mkstemp", "fsync", "replace"])
def test_failed_write_preserves_existing_bytes_and_catalog_memory(tmp_path, monkeypatch, failure_target):
    repository = JsonLabelCatalogRepository(tmp_path)
    first = repository.add("amplitude_labels", "First")
    before = repository.path.read_bytes()
    original = getattr(catalog_storage.tempfile if failure_target == "mkstemp" else catalog_storage.os, failure_target)
    def fail(*args, **kwargs):
        raise PermissionError("injected denial")
    monkeypatch.setattr(catalog_storage.tempfile if failure_target == "mkstemp" else catalog_storage.os, failure_target, fail)
    with pytest.raises(CatalogStorageError):
        repository.add("amplitude_labels", "Second")
    assert repository.path.read_bytes() == before
    assert repository.load().amplitude_labels == (first,)
    assert original  # Keeps the fixture explicit about the patched operation.


def test_failed_first_add_leaves_no_catalog_file(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError("injected denial")
    monkeypatch.setattr(catalog_storage.tempfile, "mkstemp", fail)
    with pytest.raises(CatalogStorageError):
        JsonLabelCatalogRepository(tmp_path).add("amplitude_labels", "First")
    assert list(tmp_path.iterdir()) == []


def test_exact_model_fields_reject_unknown_entry_data():
    with pytest.raises(ValidationError):
        catalog_from_dict({"schema_version": 1, "amplitude_labels": [{"id": "a", "name": "A", "color": AMPLITUDE_LABEL_COLORS[0], "extra": 1}], "interval_labels": []})
    assert catalog_from_dict(catalog_to_dict(LabelCatalog())) == LabelCatalog()
