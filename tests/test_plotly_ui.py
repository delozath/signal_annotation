from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from importlib.resources import files

import pytest

from signal_annotation.adapters import plotly_actions, plotly_ui
from signal_annotation.adapters import catalog_storage, storage
from signal_annotation.adapters.catalog_storage import JsonLabelCatalogRepository
from signal_annotation.adapters.storage import JsonSidecarRepository, StorageError, to_dict
from signal_annotation.adapters.readers import demo_recording
from signal_annotation.application import AnnotationService
from signal_annotation.domain import (
    Amplitude, Annotation, Channel, Coordinate, Label, Location, Proposal, Provenance,
    Recording, Reference, Relationship, Workspace,
)
from signal_annotation.label_catalog import AMPLITUDE_LABEL_COLORS, INTERVAL_LABEL_COLORS
from signal_annotation.validation import (
    AMPLITUDE_LABEL_COLOR_ATTRIBUTE, AMPLITUDE_LABEL_ID_ATTRIBUTE,
    AMPLITUDE_LABEL_NAME_ATTRIBUTE, GENERAL_NOTES_KEY, INTERVAL_LABEL_COLOR_ATTRIBUTE,
    INTERVAL_LABEL_ID_ATTRIBUTE, INTERVAL_LABEL_NAME_ATTRIBUTE,
)


def _component_ids(component):
    identifier = getattr(component, "id", None)
    if identifier is not None:
        yield identifier
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            yield from _component_ids(child)
    elif children is not None:
        yield from _component_ids(children)


def _component_by_id(component, identifier):
    if getattr(component, "id", None) == identifier:
        return component
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            found = _component_by_id(child, identifier)
            if found is not None:
                return found
    elif children is not None:
        return _component_by_id(children, identifier)
    return None


def _catalog_service(tmp_path):
    recording = demo_recording()
    return AnnotationService(
        recording,
        JsonSidecarRepository(tmp_path / "demo.annotations.json", recording),
        "plotly tester",
        JsonLabelCatalogRepository(tmp_path),
    )


def _durable(action, service, visible, catalog, state, *, amplitude_name="", interval_name="", notes=""):
    return plotly_ui.execute_durable_action(
        action, service, visible, catalog, state,
        amplitude_name=amplitude_name, interval_name=interval_name, notes=notes,
    )


def _selected_point_state(service, annotation):
    state = plotly_ui._initial_state(service.recording)
    figure = plotly_ui.signal_figure(service.recording, service.workspace, state)
    target = next(trace for trace in figure["data"]
                  if trace.get("meta", {}).get("annotation_id") == annotation.id)
    identity = target["meta"]
    state["selection"] = {
        "projection_id": identity["projection_id"],
        "annotation_id": annotation.id,
        "channel_id": identity["channel_id"],
        "kind": "point",
    }
    state["selected_visual"] = {"annotation_id": annotation.id, "kind": "point"}
    return state


def test_dash_shell_layout_has_unique_english_ids_and_transient_stores():
    app = plotly_ui.create_dash_app()
    ids = list(_component_ids(app.layout))

    assert set(ids) == set(plotly_ui.COMPONENT_IDS)
    assert len(ids) == len(set(ids))
    assert app.layout.id == "signal-annotation-app"
    assert "Signal annotation" in str(app.layout)
    assert app.layout.children[0].data == plotly_actions.initial_transient_state()
    assert app.layout.children[0].storage_type == "memory"
    assert app.layout.children[1].storage_type == "memory"


def test_callback_map_has_one_canonical_dispatcher_and_help_toggle():
    app = plotly_ui.create_dash_app()
    callbacks = list(app.callback_map.values())
    dispatcher = next(item for item in callbacks if str(item["output"]).find("transient-state-store.data") >= 0)

    assert {item["id"] for item in dispatcher["inputs"]} == {
        "annotation-mode-control", "bridge-event-store", "signal-graph",
        "go-time-window-button", "full-view-button", "reload-saved-state-button",
        "add-amplitude-label-button", "add-interval-label-button",
        "amplitude-label-select", "reassign-selected-point-button",
        "interval-label-select",
        "save-general-notes-button", "clear-general-notes-button", "export-csv-button",
        "create-amplitude-marker-button", "create-interval-button",
    }
    assert "status-region.children" in str(dispatcher["output"])
    assert "dispatcher-result-store.data" in str(dispatcher["output"])
    assert "signal-annotation-app.data-has-selection" in str(dispatcher["output"])
    assert "signal-graph.figure" in str(dispatcher["output"])
    assert "time-window-start-input.value" in str(dispatcher["output"])
    assert "time-window-width-input.value" in str(dispatcher["output"])
    assert "amplitude-label-select.options" in str(dispatcher["output"])
    assert "amplitude-label-select.value" in str(dispatcher["output"])
    assert "interval-label-select.options" in str(dispatcher["output"])
    assert "interval-label-select.value" in str(dispatcher["output"])
    assert "general-notes-input.value" in str(dispatcher["output"])
    assert "active-channel-context.children" in str(dispatcher["output"])
    assert "point-time-input.value" in str(dispatcher["output"])
    assert "interval-start-input.value" in str(dispatcher["output"])
    assert "interval-end-input.value" in str(dispatcher["output"])
    assert sum("status-region.children" in str(item["output"]) for item in callbacks) == 1
    assert plotly_ui.help_dialog_open(plotly_ui.HELP_OPEN_ID, False) is True
    assert plotly_ui.help_dialog_open(plotly_ui.HELP_CLOSE_ID, True) is False
    assert plotly_ui.help_dialog_open(None, True) is True


def test_injected_recording_renders_signal_only_figure_and_navigation_controls():
    app = plotly_ui.create_dash_app(demo_recording())
    graph = next(
        component for component in app.layout.children
        if getattr(component, "className", None) == "plot-region"
    ).children
    figure = graph.figure

    assert len(figure["data"]) == 6
    assert all(trace["mode"] == "lines" for trace in figure["data"])
    assert figure["layout"]["xaxis"]["range"] == [0.0, 8.0]
    assert figure["layout"]["xaxis6"]["rangeslider"] == {"visible": True}
    assert graph.config["scrollZoom"] is True
    assert graph.config["displaylogo"] is False


def test_navigation_actions_normalize_native_ranges_and_keep_one_bounded_viewport():
    variants = (
        {"xaxis.range[0]": 3.0, "xaxis.range[1]": 11.0},
        {"xaxis2.range[0]": 3.0, "xaxis2.range[1]": 11.0},
        {"xaxis3.range": [3.0, 11.0]},
    )
    for relayout in variants:
        action = plotly_actions.normalize_action("signal-graph", relayout)
        state, status, result = plotly_actions.dispatch(action, {}, duration=12.0)
        assert state["viewport"] == {
            "start": 3.0, "end": 11.0, "width": 8.0, "duration": 12.0,
        }
        assert status == "Time view updated."
        assert result["kind"] == "set-viewport"

    full = plotly_actions.normalize_action("signal-graph", {"xaxis.autorange": True})
    state, status, result = plotly_actions.dispatch(full, state, duration=12.0)
    assert state["viewport"] == {
        "start": 0.0, "end": 12.0, "width": 12.0, "duration": 12.0,
    }
    assert status == "Full view."
    assert result["kind"] == "full-view"

    go = plotly_actions.normalize_action(
        "go-time-window-button", {"start": 10.0, "end": 18.0}
    )
    state, _, _ = plotly_actions.dispatch(go, state, duration=12.0)
    assert state["viewport"] == {
        "start": 4.0, "end": 12.0, "width": 8.0, "duration": 12.0,
    }
    figure = plotly_ui.signal_figure(demo_recording(), None, state)
    assert all(
        figure["layout"]["xaxis" + ("" if index == 1 else str(index))]["range"]
        == [4.0, 12.0]
        for index in range(1, 7)
    )
    assert plotly_actions.normalize_action("signal-graph", {"yaxis.range[0]": 2}) is None
    assert plotly_actions.normalize_action(None, None) is None


def test_dispatcher_is_pure_and_has_no_durable_action_path():
    state = plotly_actions.initial_transient_state()
    action = plotly_actions.normalize_action("annotation-mode-control", "mark-interval")
    next_state, status, result = plotly_actions.dispatch(action, state)

    assert state["mode"] == "navigate"
    assert next_state["mode"] == "mark-interval"
    assert status == "Mode: mark-interval."
    assert result == {"kind": "set-mode"}
    bridge = plotly_actions.normalize_action("bridge-event-store", {"type": "delete-request"})
    assert plotly_actions.dispatch(bridge, next_state)[2] == {"kind": "delete-request"}
    ignored_bridge = plotly_actions.normalize_action(
        "bridge-event-store", {"type": "graph-click", "identity": ["stale"]},
    )
    retained, _, ignored = plotly_actions.dispatch(ignored_bridge, next_state)
    assert retained == next_state and ignored == {"kind": "ignored-bridge-graph-click"}
    reload_action = plotly_actions.normalize_action("reload-saved-state-button", 1)
    assert plotly_actions.dispatch(reload_action, next_state)[2] == {"kind": "reload"}
    assert next_state["selected_visual"] == {"annotation_id": None, "kind": None}


def test_service_only_injection_and_validated_reload_preserve_visible_state_on_failure(tmp_path):
    recording = demo_recording()
    sidecar = tmp_path / "demo.annotations.json"
    service = AnnotationService(recording, JsonSidecarRepository(sidecar, recording), "plotly tester")
    app = plotly_ui.create_dash_app(service=service)
    graph = next(component for component in app.layout.children
                 if getattr(component, "className", None) == "plot-region").children
    assert len(graph.figure["data"]) == len(recording.channels)

    writer = AnnotationService(recording, JsonSidecarRepository(sidecar, recording), "external writer")
    added = writer.add("maximum", "ch0", 1.0, None, "Maximum")
    region = Annotation(
        "region", Location("region", ("ch1",), Coordinate(.5), Coordinate(1.5),
                           Amplitude("mV", minimum=-2.0, maximum=2.0)),
        Label("artifact", "Region"), Provenance("manual", "external writer", "2026-01-01T00:00:00+00:00"),
        {"region_extension": ["preserve"]},
    )
    recording_annotation = Annotation(
        "recording", Location("recording"), Label("quality", "Usable"),
        Provenance("manual", "external writer", "2026-01-01T00:00:00+00:00"),
        {"recording_extension": True},
    )
    proposal = Proposal(
        "proposal", Location("interval", ("ch2",), Coordinate(.5), Coordinate(1.5)),
        Label("interval", "Proposed"), "model", "1", .8,
        "2026-01-01T00:00:00+00:00", extensions={"proposal_extension": {"keep": 1}},
    )
    relationship = Relationship(
        "relationship", Reference("annotation", added.id), Reference("annotation", region.id),
        Label("relates", "Relates"), {"relationship_extension": "keep"},
    )
    durable = Workspace(
        recording.id, [added, region, recording_annotation], [proposal], [relationship],
        {"general_notes": "Preserve this", "custom_metadata": {"nested": [1, 2]}},
        {"custom_extension": {"unchanged": True}},
    )
    writer.repository.save(durable)
    committed_bytes = sidecar.read_bytes()
    visible, status = plotly_ui.reload_visible_workspace(service, Workspace(recording.id))
    assert status == "Reloaded saved annotations."
    assert visible == service.workspace
    assert visible == durable
    assert json.loads(committed_bytes) == to_dict(visible, recording)
    assert sidecar.read_bytes() == committed_bytes

    prior_figure = plotly_ui.signal_figure(recording, visible, plotly_actions.initial_transient_state())
    sidecar.write_bytes(b"{broken")
    corrupt_bytes = sidecar.read_bytes()
    retained, failure = plotly_ui.reload_visible_workspace(service, visible)
    assert retained is visible
    assert retained == visible
    assert failure.startswith("Reload failed: Corrupt JSON.")
    assert sidecar.read_bytes() == corrupt_bytes
    assert plotly_ui.signal_figure(recording, retained, plotly_actions.initial_transient_state()) == prior_figure


def test_factory_rejects_inconsistent_injected_state(tmp_path):
    recording = demo_recording()
    service = AnnotationService(
        recording, JsonSidecarRepository(tmp_path / "demo.annotations.json", recording), "plotly tester"
    )
    other_recording = Recording("other", "other.acq", (
        Channel("other", "Other", "mV", 1.0, (0.0, 1.0)),
    ))
    with pytest.raises(ValueError, match="recording"):
        plotly_ui.create_dash_app(recording=other_recording, service=service)
    with pytest.raises(ValueError, match="workspace"):
        plotly_ui.create_dash_app(recording=recording, workspace=Workspace("other"), service=service)
    with pytest.raises(ValueError, match="workspace"):
        plotly_ui.create_dash_app(recording=recording, workspace=Workspace("other"))


def test_local_assets_are_package_data_and_bridge_is_focus_safe_observation_only():
    assets = files("signal_annotation").joinpath("assets")
    assert assets.joinpath("plotly.css").is_file()
    bridge = assets.joinpath("plotly_events.js").read_text(encoding="utf-8")

    assert "input, textarea, select, button, [contenteditable]" in bridge
    assert 'event.key === "Delete"' in bridge
    assert 'event.key === "Backspace"' in bridge
    assert "ctrlKey" in bridge and "metaKey" in bridge and "point.customdata" in bridge
    assert "window.dash_clientside.set_props" in bridge
    assert "app.dataset.hasSelection === \"true\"" in bridge
    assert "graph.querySelector(\".js-plotly-plot\")" in bridge
    for forbidden in ("Plotly.relayout", "getBoundingClientRect", "fetch(", "XMLHttpRequest", "WebSocket", "localStorage", "sessionStorage", ".style."):
        assert forbidden not in bridge


def test_catalog_controls_lazily_add_independent_collections_and_keep_stable_selections(tmp_path):
    service = _catalog_service(tmp_path)
    root_path = service.catalog_repository.path
    app = plotly_ui.create_dash_app(service=service)

    assert not root_path.exists()
    assert _component_by_id(app.layout, plotly_ui.AMPLITUDE_SELECT_ID).options == []
    assert _component_by_id(app.layout, plotly_ui.INTERVAL_SELECT_ID).options == []
    assert _component_by_id(app.layout, plotly_ui.AMPLITUDE_STATUS_ID).children == "0/10 labels"
    assert _component_by_id(app.layout, plotly_ui.INTERVAL_STATUS_ID).children == "0/10 labels"

    before_first_sidecar = service.repository.path.read_bytes()
    visible, catalog, state, status, result, notes, amplitude_name, interval_name = _durable(
        plotly_actions.normalize_action(plotly_ui.AMPLITUDE_ADD_BUTTON_ID, "Maximum"),
        service, service.workspace, service.catalog, plotly_actions.initial_transient_state(),
        amplitude_name="Maximum", notes="unsaved notes",
    )
    entry = catalog.amplitude_labels[0]
    assert root_path.exists()
    assert service.repository.path.read_bytes() == before_first_sidecar
    assert status == "Amplitude label added."
    assert result == {"kind": "add-amplitude-label"}
    assert state["selected_amplitude_catalog_id"] == entry.id
    assert amplitude_name == ""
    assert notes == "unsaved notes"
    assert plotly_ui.catalog_options(catalog, "amplitude_labels") == [
        {"label": f"Maximum — {AMPLITUDE_LABEL_COLORS[0]}", "value": entry.id}
    ]
    assert plotly_ui.catalog_status(catalog, "amplitude_labels") == "1/10 labels"

    before_sidecar = service.repository.path.read_bytes()
    for index in range(1, 10):
        visible, catalog, state, _, _, _, _, _ = _durable(
            plotly_actions.normalize_action(plotly_ui.AMPLITUDE_ADD_BUTTON_ID, f"Amplitude {index}"),
            service, visible, catalog, state, amplitude_name=f"Amplitude {index}", notes=notes,
        )
    for index in range(10):
        visible, catalog, state, _, _, _, _, _ = _durable(
            plotly_actions.normalize_action(plotly_ui.INTERVAL_ADD_BUTTON_ID, f"Interval {index}"),
            service, visible, catalog, state, interval_name=f"Interval {index}", notes=notes,
        )
    assert service.repository.path.read_bytes() == before_sidecar
    assert [item.color for item in catalog.amplitude_labels] == list(AMPLITUDE_LABEL_COLORS)
    assert [item.color for item in catalog.interval_labels] == list(INTERVAL_LABEL_COLORS)
    assert len({item.id for item in catalog.amplitude_labels + catalog.interval_labels}) == 20
    assert state["selected_interval_catalog_id"] == catalog.interval_labels[-1].id
    before_root = root_path.read_bytes()
    before_state = deepcopy(state)
    failed = _durable(
        plotly_actions.normalize_action(plotly_ui.AMPLITUDE_ADD_BUTTON_ID, "maximum"),
        service, visible, catalog, state, amplitude_name="maximum", notes="draft notes",
    )
    assert failed[0] is visible and failed[1] == catalog and failed[2] == before_state
    assert failed[3].startswith("Add amplitude label failed:")
    assert failed[5:] == ("draft notes", "maximum", "")
    assert root_path.read_bytes() == before_root
    failed = _durable(
        plotly_actions.normalize_action(plotly_ui.AMPLITUDE_ADD_BUTTON_ID, "Eleventh"),
        service, visible, catalog, state, amplitude_name="Eleventh",
    )
    assert "At most 10 labels" in failed[3]
    assert root_path.read_bytes() == before_root

    other_recording = Recording("other-recording", "other.acq", (
        Channel("other", "Other", "mV", 10.0, tuple(float(index) for index in range(21))),
    ))
    other = AnnotationService(
        other_recording, JsonSidecarRepository(tmp_path / "other.annotations.json", other_recording),
        "other tester", JsonLabelCatalogRepository(tmp_path),
    )
    assert [item.id for item in other.catalog.amplitude_labels] == [item.id for item in catalog.amplitude_labels]
    assert [item.id for item in other.catalog.interval_labels] == [item.id for item in catalog.interval_labels]


def test_notes_reload_and_csv_use_current_workspace_and_preserve_siblings(tmp_path):
    service = _catalog_service(tmp_path)
    candidate = deepcopy(service.workspace)
    candidate.metadata = {"study": {"protocol": "keep"}, "other": [1, None]}
    service.repository.save(candidate)
    service.reload()
    visible, catalog, state = service.workspace, service.catalog, plotly_actions.initial_transient_state()

    visible, catalog, state, status, _, note_text, _, _ = _durable(
        plotly_actions.normalize_action(plotly_ui.SAVE_NOTES_BUTTON_ID, "First\nExact note"),
        service, visible, catalog, state, notes="First\nExact note",
    )
    assert status == "General notes saved."
    assert note_text == "First\nExact note"
    assert visible.metadata == {"study": {"protocol": "keep"}, "other": [1, None], GENERAL_NOTES_KEY: note_text}
    visible, catalog, state, status, _, note_text, _, _ = _durable(
        plotly_actions.normalize_action(plotly_ui.CLEAR_NOTES_BUTTON_ID, None),
        service, visible, catalog, state, notes="First\nExact note",
    )
    assert status == "General notes cleared." and note_text == ""
    assert visible.metadata == {"study": {"protocol": "keep"}, "other": [1, None], GENERAL_NOTES_KEY: ""}

    marker = service.add("maximum", "ch0", 1.0, None, "Maximum")
    csv_path = service.repository.path.with_suffix(".csv")
    csv_path.write_bytes(b"old CSV")
    visible, catalog, state, status, exported, _, _, _ = _durable(
        plotly_actions.normalize_action(plotly_ui.EXPORT_CSV_BUTTON_ID, None), service, visible, catalog, state,
    )
    first_csv = csv_path.read_bytes()
    assert str(csv_path) in status and exported == {"kind": "export-csv", "path": str(csv_path)}
    assert first_csv.startswith(b"\xef\xbb\xbf") and b"old CSV" not in first_csv and marker.id.encode() in first_csv
    service.add("minimum", "ch0", 2.0, None, "Minimum")
    _durable(plotly_actions.normalize_action(plotly_ui.EXPORT_CSV_BUTTON_ID, None), service, visible, catalog, state)
    assert csv_path.read_bytes() != first_csv

    outside_catalog = JsonLabelCatalogRepository(tmp_path)
    outside_catalog.add("interval_labels", "External")
    external = AnnotationService(service.recording,
        JsonSidecarRepository(service.repository.path, service.recording), "external", JsonLabelCatalogRepository(tmp_path))
    external.save_general_notes("Reloaded note")
    state["selected_amplitude_catalog_id"] = "deleted-amplitude"
    state["selected_interval_catalog_id"] = "deleted-interval"
    visible, catalog, state, status, _, note_text, _, _ = _durable(
        plotly_actions.normalize_action(plotly_ui.RELOAD_BUTTON_ID, None), service, visible, catalog, state,
        amplitude_name="draft label", interval_name="draft interval", notes="unsaved",
    )
    assert status == "Reloaded saved annotations." and note_text == "Reloaded note"
    assert catalog.interval_labels[-1].name == "External"
    assert state["selected_amplitude_catalog_id"] is None
    assert state["selected_interval_catalog_id"] is None


def test_durable_conflict_and_io_failures_retain_visible_drafts_bytes_and_service_state(tmp_path, monkeypatch):
    service = _catalog_service(tmp_path)
    service.add_amplitude_label("First")
    visible, catalog, state = service.workspace, service.catalog, plotly_actions.initial_transient_state()
    other_catalog = JsonLabelCatalogRepository(tmp_path)
    other_catalog.add("amplitude_labels", "Outside")
    before_sidecar, before_root, before_catalog = service.repository.path.read_bytes(), service.catalog_repository.path.read_bytes(), service.catalog
    failed = _durable(
        plotly_actions.normalize_action(plotly_ui.AMPLITUDE_ADD_BUTTON_ID, "Stale"),
        service, visible, catalog, state, amplitude_name="Stale", interval_name="Interval draft", notes="Note draft",
    )
    assert "changed in another program" in failed[3]
    assert failed[0] is visible and failed[1] == before_catalog and failed[2] == state
    assert failed[5:] == ("Note draft", "Stale", "Interval draft")
    assert service.repository.path.read_bytes() == before_sidecar
    assert service.catalog_repository.path.read_bytes() == before_root

    external = AnnotationService(service.recording,
        JsonSidecarRepository(service.repository.path, service.recording), "external")
    external.save_general_notes("written elsewhere")
    sidecar_conflict_bytes = service.repository.path.read_bytes()
    failed = _durable(
        plotly_actions.normalize_action(plotly_ui.SAVE_NOTES_BUTTON_ID, "Stale notes"),
        service, visible, catalog, state, notes="Stale notes",
    )
    assert "changed in another tab/program" in failed[3]
    assert failed[0] is visible and failed[5] == "Stale notes"
    assert service.repository.path.read_bytes() == sidecar_conflict_bytes
    service.reload()

    def catalog_denied(*args, **kwargs):
        raise catalog_storage.CatalogStorageError("injected catalog failure")

    io_root = tmp_path / "io-catalog"
    io_root.mkdir()
    fresh = _catalog_service(io_root)
    before_root = None
    monkeypatch.setattr(catalog_storage, "atomic_catalog_text", catalog_denied)
    failed = _durable(plotly_actions.normalize_action(plotly_ui.AMPLITUDE_ADD_BUTTON_ID, "Denied"),
                      fresh, fresh.workspace, fresh.catalog, plotly_actions.initial_transient_state(),
                      amplitude_name="Denied", notes="keep")
    assert "injected catalog failure" in failed[3] and failed[6] == "Denied"
    assert not fresh.catalog_repository.path.exists() and fresh.repository.path.read_bytes()

    monkeypatch.undo()
    visible, catalog, state = service.workspace, service.catalog, plotly_actions.initial_transient_state()
    before_sidecar = service.repository.path.read_bytes()

    def sidecar_denied(*args, **kwargs):
        raise StorageError("injected sidecar failure")

    monkeypatch.setattr(storage, "atomic_text", sidecar_denied)
    failed = _durable(plotly_actions.normalize_action(plotly_ui.SAVE_NOTES_BUTTON_ID, "Draft notes"),
                      service, visible, catalog, state, notes="Draft notes")
    assert "injected sidecar failure" in failed[3] and failed[5] == "Draft notes"
    assert service.repository.path.read_bytes() == before_sidecar
    csv_path = service.repository.path.with_suffix(".csv")
    csv_path.write_bytes(b"prior CSV")
    failed = _durable(plotly_actions.normalize_action(plotly_ui.EXPORT_CSV_BUTTON_ID, None),
                      service, visible, catalog, state, notes="Draft notes")
    assert "injected sidecar failure" in failed[3]
    assert csv_path.read_bytes() == b"prior CSV"


def test_service_less_durable_buttons_leave_empty_shell_state_safe():
    state = plotly_actions.initial_transient_state()
    result = _durable(
        plotly_actions.normalize_action(plotly_ui.EXPORT_CSV_BUTTON_ID, None),
        None, None, plotly_ui._service_catalog(None), state, notes="draft",
    )
    assert result[0] is None and result[2] == state
    assert result[3] == "This action requires an annotation service."
    assert result[5] == "draft"


def test_numeric_creation_initializes_and_updates_active_channel_from_native_customdata():
    recording = demo_recording()
    app = plotly_ui.create_dash_app(recording)
    state = app.layout.children[0].data
    assert state["active_channel"] == "ch0"
    assert _component_by_id(app.layout, plotly_ui.ACTIVE_CHANNEL_ID).children == (
        "Active channel: Synthetic ECG I (mV)"
    )

    signal_click = {
        "points": [{"x": .2, "customdata": ["channel/ch3", "ch3", "signal"]}],
    }
    action = plotly_actions.normalize_action("signal-graph-click", signal_click)
    updated, status, result = plotly_actions.dispatch(
        action, state, active_channel_ids=tuple(channel.id for channel in recording.channels[:8]),
    )
    assert status == "Active channel updated."
    assert result == {"kind": "set-active-channel", "channel_id": "ch3"}
    assert updated["active_channel"] == "ch3"
    assert updated["selection"] is None

    annotation_click = {
        "points": [{"customdata": ["point/example/channel/ch4", "example", "ch4", "point"]}],
    }
    annotation_action = plotly_actions.normalize_action("signal-graph-click", annotation_click)
    updated, _, _ = plotly_actions.dispatch(
        annotation_action, updated,
        active_channel_ids=tuple(channel.id for channel in recording.channels[:8]),
    )
    assert updated["active_channel"] == "ch4" and updated["selection"] is None

    unknown = plotly_actions.normalize_action(
        "signal-graph-click", {"points": [{
            "x": .2, "customdata": ["channel/no", "no", "signal"],
        }]}
    )
    retained, _, result = plotly_actions.dispatch(
        unknown, updated, active_channel_ids=tuple(channel.id for channel in recording.channels[:8]),
    )
    assert retained == updated and result == {"kind": "ignored-active-channel"}
    assert plotly_actions.normalize_action("signal-graph-click", {"points": [{"curveNumber": 2}]}) is None


def test_numeric_catalog_creation_persists_snaps_reopens_and_retains_catalog_choices(tmp_path, monkeypatch):
    service = _catalog_service(tmp_path)
    amplitude = service.add_amplitude_label("Peak")
    interval = service.add_interval_label("Artifact")
    initial = plotly_actions.initial_transient_state()
    initial.update({
        "mode": "mark-amplitude",
        "active_channel": "ch0",
        "selected_amplitude_catalog_id": amplitude["id"],
        "selected_interval_catalog_id": interval["id"],
    })
    saves = []
    original_save = service.repository.save

    def counted_save(candidate):
        saves.append(candidate)
        return original_save(candidate)

    monkeypatch.setattr(service.repository, "save", counted_save)
    point_action = plotly_actions.normalize_action(plotly_ui.CREATE_POINT_BUTTON_ID, .401)
    visible, catalog, state, status, result = plotly_ui.execute_numeric_creation(
        point_action, service, service.workspace, service.catalog, initial,
    )
    assert status == "Amplitude point created." and result == {"kind": "create-amplitude-marker"}
    assert len(saves) == 1 and state["selected_amplitude_catalog_id"] == amplitude["id"]
    marker = visible.annotations[-1]
    assert marker.start == pytest.approx(.402)
    assert marker.label.text == "Peak"
    assert marker.label.attributes == {
        AMPLITUDE_LABEL_ID_ATTRIBUTE: amplitude["id"],
        AMPLITUDE_LABEL_NAME_ATTRIBUTE: "Peak",
        AMPLITUDE_LABEL_COLOR_ATTRIBUTE: amplitude["color"],
    }
    root_bytes = service.catalog_repository.path.read_bytes()
    sidecar_after_point = service.repository.path.read_bytes()

    state["mode"] = "mark-interval"
    interval_action = plotly_actions.normalize_action(
        plotly_ui.CREATE_INTERVAL_BUTTON_ID, {"start": .3, "end": .9},
    )
    visible, catalog, state, status, result = plotly_ui.execute_numeric_creation(
        interval_action, service, visible, catalog, state,
    )
    assert status == "Interval created." and result == {"kind": "create-interval"}
    assert len(saves) == 2 and service.catalog_repository.path.read_bytes() == root_bytes
    band = visible.annotations[-1]
    assert band.channel_id == "ch0" and (band.start, band.end) == (.3, .9)
    assert band.label.attributes == {
        INTERVAL_LABEL_ID_ATTRIBUTE: interval["id"],
        INTERVAL_LABEL_NAME_ATTRIBUTE: "Artifact",
        INTERVAL_LABEL_COLOR_ATTRIBUTE: interval["color"],
    }
    reopened = AnnotationService(
        service.recording, JsonSidecarRepository(service.repository.path, service.recording),
        "reopen", JsonLabelCatalogRepository(tmp_path),
    )
    assert reopened.workspace == visible and reopened.catalog == catalog
    assert sidecar_after_point != service.repository.path.read_bytes()
    assert plotly_ui.numeric_drafts_after_creation(
        {"kind": "create-amplitude-marker"}, .4, .2, .8,
    ) == (None, .2, .8)
    assert plotly_ui.numeric_drafts_after_creation(
        {"kind": "create-interval"}, .4, .2, .8,
    ) == (.4, None, None)
    assert plotly_ui.numeric_drafts_after_creation(
        {"kind": "create-interval-failed"}, .4, .2, .8,
    ) == (.4, .2, .8)


def test_numeric_creation_rejects_every_invalid_command_without_writes_or_visible_change(tmp_path):
    service = _catalog_service(tmp_path)
    amplitude = service.add_amplitude_label("Peak")
    interval = service.add_interval_label("Artifact")
    initial = plotly_actions.initial_transient_state()
    initial.update({
        "mode": "mark-amplitude", "active_channel": "ch0",
        "selected_amplitude_catalog_id": amplitude["id"],
        "selected_interval_catalog_id": interval["id"],
    })
    visible, catalog = service.workspace, service.catalog
    sidecar_bytes, root_bytes = service.repository.path.read_bytes(), service.catalog_repository.path.read_bytes()
    prior_figure = plotly_ui.signal_figure(service.recording, visible, initial)
    cases = [
        ({"kind": "create-amplitude-marker", "time": .2}, {**initial, "mode": "navigate"}),
        ({"kind": "create-interval", "start": .2, "end": .4}, initial),
        ({"kind": "create-amplitude-marker", "time": .2}, {**initial, "selected_amplitude_catalog_id": None}),
        ({"kind": "create-amplitude-marker", "time": .2}, {**initial, "selected_amplitude_catalog_id": "stale"}),
        ({"kind": "create-interval", "start": .2, "end": .4}, {**initial, "mode": "mark-interval", "selected_interval_catalog_id": None}),
        ({"kind": "create-interval", "start": .2, "end": .4}, {**initial, "mode": "mark-interval", "selected_interval_catalog_id": "stale"}),
        ({"kind": "create-amplitude-marker", "time": .2}, {**initial, "active_channel": "missing"}),
        ({"kind": "create-amplitude-marker", "time": float("nan")}, initial),
        ({"kind": "create-amplitude-marker", "time": 1000.0}, initial),
        ({"kind": "create-interval", "start": .8, "end": .2}, {**initial, "mode": "mark-interval"}),
        ({"kind": "create-interval", "start": .2, "end": float("inf")}, {**initial, "mode": "mark-interval"}),
        ({"kind": "create-interval", "start": .2, "end": 1000.0}, {**initial, "mode": "mark-interval"}),
    ]
    for action, state in cases:
        before_state = deepcopy(state)
        result = plotly_ui.execute_numeric_creation(action, service, visible, catalog, state)
        assert result[0] is visible and result[1] == catalog and result[2] == before_state
        assert result[4]["kind"].endswith("-failed")
        assert service.repository.path.read_bytes() == sidecar_bytes
        assert service.catalog_repository.path.read_bytes() == root_bytes
        assert plotly_ui.signal_figure(service.recording, visible, result[2]) == prior_figure

    unavailable = plotly_ui.execute_numeric_creation(
        {"kind": "create-amplitude-marker", "time": .2}, None, None,
        plotly_ui._service_catalog(None), initial,
    )
    assert unavailable[0] is None and unavailable[2] == initial
    assert unavailable[3] == "This action requires an annotation service."


def test_numeric_point_model_constraint_rolls_back_for_nonfinite_sample(tmp_path):
    recording = Recording("nonfinite", "nonfinite.acq", (
        Channel("channel", "Channel", "mV", 2.0, (0.0, float("nan"), 2.0)),
    ))
    service = AnnotationService(
        recording, JsonSidecarRepository(tmp_path / "nonfinite.annotations.json", recording),
        "plotly tester", JsonLabelCatalogRepository(tmp_path),
    )
    amplitude = service.add_amplitude_label("Peak")
    state = plotly_actions.initial_transient_state()
    state.update({
        "mode": "mark-amplitude", "active_channel": "channel",
        "selected_amplitude_catalog_id": amplitude["id"],
    })
    sidecar_bytes, root_bytes = service.repository.path.read_bytes(), service.catalog_repository.path.read_bytes()
    result = plotly_ui.execute_numeric_creation(
        {"kind": "create-amplitude-marker", "time": .5}, service,
        service.workspace, service.catalog, state,
    )
    assert result[0] is service.workspace and result[2] == state
    assert "finite value" in result[3]
    assert service.repository.path.read_bytes() == sidecar_bytes
    assert service.catalog_repository.path.read_bytes() == root_bytes


def test_native_signal_click_creates_exactly_one_snapped_catalog_point(tmp_path, monkeypatch):
    service = _catalog_service(tmp_path)
    amplitude = service.add_amplitude_label("Peak")
    state = plotly_ui._initial_state(service.recording)
    state.update({
        "mode": "mark-amplitude",
        "selected_amplitude_catalog_id": amplitude["id"],
    })
    figure = plotly_ui.signal_figure(service.recording, service.workspace, state)
    signal = next(trace for trace in figure["data"]
                  if trace["meta"].get("channel_id") == "ch2" and
                  trace["meta"].get("kind") is None)
    index = min(137, len(signal["x"]) - 1)
    click = {"points": [{
        "x": signal["x"][index],
        "y": signal["y"][index],
        "customdata": signal["customdata"][index],
    }]}
    action = plotly_actions.normalize_action("signal-graph-click", click)
    action = plotly_ui.validate_native_graph_action(
        action, service.recording, service.workspace, state,
    )
    assert action is not None and action["graph_target"] == "signal"
    updated, _, _ = plotly_actions.dispatch(
        action, state,
        active_channel_ids=tuple(channel.id for channel in service.recording.channels[:8]),
    )
    saves = []
    original_save = service.repository.save

    def counted_save(candidate):
        saves.append(candidate)
        return original_save(candidate)

    monkeypatch.setattr(service.repository, "save", counted_save)
    root_bytes = service.catalog_repository.path.read_bytes()
    visible, catalog, updated, status, result = plotly_ui.execute_graphical_marker_click(
        action, service, service.workspace, service.catalog, updated, state,
    )

    assert status == "Amplitude point created."
    assert result == {"kind": "graph-create-amplitude-marker"}
    assert len(saves) == 1 and len(visible.annotations) == 1
    assert service.catalog_repository.path.read_bytes() == root_bytes
    marker = visible.annotations[0]
    expected = service.recording.channel("ch2").sample_at(signal["x"][index])
    assert marker.channel_id == updated["active_channel"] == "ch2"
    assert (marker.location.start.sample_index, marker.start, marker.value) == expected
    assert marker.label.text == "Peak"
    assert marker.label.attributes[AMPLITUDE_LABEL_ID_ATTRIBUTE] == amplitude["id"]
    assert updated["selection"] is None

    # The retained observer event has no durable path after the canonical native click.
    bridge = plotly_actions.normalize_action(
        plotly_ui.BRIDGE_ID, {"type": "graph-click", "identity": signal["customdata"][index]},
    )
    retained, _, bridge_result = plotly_actions.dispatch(bridge, updated)
    assert retained == updated
    assert bridge_result == {"kind": "ignored-bridge-graph-click"}
    assert len(saves) == 1


def test_native_click_callback_commits_once_and_duplicate_bridge_is_all_no_update(tmp_path, monkeypatch):
    service = _catalog_service(tmp_path)
    amplitude = service.add_amplitude_label("Peak")
    app = plotly_ui.create_dash_app(service=service)
    graph = _component_by_id(app.layout, plotly_ui.GRAPH_ID)
    state = deepcopy(_component_by_id(app.layout, plotly_ui.STATE_ID).data)
    state.update({
        "mode": "mark-amplitude",
        "selected_amplitude_catalog_id": amplitude["id"],
    })
    signal = next(trace for trace in graph.figure["data"]
                  if trace["meta"].get("channel_id") == "ch1" and
                  trace["meta"].get("kind") is None)
    click = {"points": [{
        "x": signal["x"][123], "y": signal["y"][123],
        "customdata": signal["customdata"][123],
    }]}
    callback_key, callback = next(
        (key, value) for key, value in app.callback_map.items()
        if "transient-state-store.data" in key
    )
    outputs = [
        {"id": output.component_id, "property": output.component_property}
        for output in callback["output"]
    ]

    def payload(changed, *, bridge=None, native_click=click, transient=state):
        overrides = {
            (plotly_ui.MODE_ID, "value"): "mark-amplitude",
            (plotly_ui.BRIDGE_ID, "data"): bridge,
            (plotly_ui.GRAPH_ID, "clickData"): native_click,
            (plotly_ui.AMPLITUDE_SELECT_ID, "value"): amplitude["id"],
            (plotly_ui.STATE_ID, "data"): transient,
        }

        def item(spec):
            key = (spec["id"], spec["property"])
            component = _component_by_id(app.layout, spec["id"])
            return {
                "id": spec["id"],
                "property": spec["property"],
                "value": overrides.get(key, getattr(component, spec["property"], None)),
            }

        return {
            "output": callback_key,
            "outputs": outputs,
            "changedPropIds": [changed],
            "inputs": [item(spec) for spec in callback["inputs"]],
            "state": [item(spec) for spec in callback["state"]],
        }

    saves = []
    original_save = service.repository.save

    def counted_save(candidate):
        saves.append(candidate)
        return original_save(candidate)

    monkeypatch.setattr(service.repository, "save", counted_save)
    client = app.server.test_client()
    response = client.post("/_dash-update-component", json=payload("signal-graph.clickData"))
    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body[plotly_ui.DISPATCH_ID]["data"] == {"kind": "graph-create-amplitude-marker"}
    committed_state = body[plotly_ui.STATE_ID]["data"]
    assert committed_state["active_channel"] == "ch1"
    assert len(saves) == len(service.workspace.annotations) == 1

    bridge = {
        "type": "graph-click",
        "identity": signal["customdata"][123],
        "nonce": 1,
    }
    response = client.post(
        "/_dash-update-component",
        json=payload("bridge-event-store.data", bridge=bridge, transient=committed_state),
    )
    assert response.status_code in {200, 204}
    if response.status_code == 200:
        assert response.get_json()["response"] == {}
    assert len(saves) == len(service.workspace.annotations) == 1


def test_native_point_click_selects_one_stable_point_without_persistence(tmp_path, monkeypatch):
    service = _catalog_service(tmp_path)
    amplitude = service.add_amplitude_label("Peak")
    first = service.add_catalog_marker("ch0", .4, amplitude["id"])
    second = service.add_catalog_marker("ch1", .8, amplitude["id"])
    state = plotly_ui._initial_state(service.recording)
    figure = plotly_ui.signal_figure(service.recording, service.workspace, state)
    target = next(trace for trace in figure["data"]
                  if trace["meta"].get("annotation_id") == second.id)
    click = {"points": [{
        "x": target["x"][0], "y": target["y"][0],
        "customdata": target["customdata"][0],
    }]}
    action = plotly_ui.validate_native_graph_action(
        plotly_actions.normalize_action("signal-graph-click", click),
        service.recording, service.workspace, state,
    )
    updated, _, _ = plotly_actions.dispatch(
        action, state,
        active_channel_ids=tuple(channel.id for channel in service.recording.channels[:8]),
    )
    saves = []
    monkeypatch.setattr(service.repository, "save", lambda candidate: saves.append(candidate))
    sidecar_bytes = service.repository.path.read_bytes()
    visible, catalog, selected, status, result = plotly_ui.execute_graphical_marker_click(
        action, service, service.workspace, service.catalog, updated, state,
    )

    assert visible is service.workspace and catalog == service.catalog and saves == []
    assert service.repository.path.read_bytes() == sidecar_bytes
    assert status == "Amplitude point selected."
    assert result == {"kind": "select-point", "annotation_id": second.id}
    assert selected["active_channel"] == "ch1"
    assert selected["selection"] == {
        "projection_id": target["meta"]["projection_id"],
        "annotation_id": second.id,
        "channel_id": "ch1",
        "kind": "point",
    }
    assert selected["selected_visual"] == {"annotation_id": second.id, "kind": "point"}
    selected_figure = plotly_ui.signal_figure(service.recording, visible, selected)
    point_traces = [trace for trace in selected_figure["data"]
                    if trace.get("meta", {}).get("kind") == "point"]
    assert sum(trace["marker"]["size"] == 12 and trace["marker"]["line"]["width"] == 2
               for trace in point_traces) == 1
    assert next(trace for trace in point_traces
                if trace["meta"]["annotation_id"] == first.id)["marker"]["size"] == 10

    moved, _, _ = plotly_actions.dispatch(
        {"kind": "set-viewport", "start": 2.0, "end": 4.0}, selected,
        duration=service.recording.duration,
    )
    redrawn = plotly_ui.signal_figure(service.recording, visible, moved)
    highlighted = next(trace for trace in redrawn["data"]
                       if trace.get("meta", {}).get("annotation_id") == second.id)
    assert moved["selection"] == selected["selection"]
    assert highlighted["marker"]["size"] == 12
    assert service.repository.path.read_bytes() == sidecar_bytes and saves == []


def test_native_marker_clicks_reject_invalid_or_wrong_targets_without_writes(tmp_path, monkeypatch):
    service = _catalog_service(tmp_path)
    amplitude = service.add_amplitude_label("Peak")
    interval = service.add_interval_label("Artifact")
    marker = service.add_catalog_marker("ch0", .4, amplitude["id"])
    band = service.add_catalog_interval("ch0", .2, .7, interval["id"])
    state = plotly_ui._initial_state(service.recording)
    state.update({
        "mode": "mark-amplitude",
        "active_channel": "ch1",
        "selected_amplitude_catalog_id": None,
    })
    figure = plotly_ui.signal_figure(service.recording, service.workspace, state)
    signal = next(trace for trace in figure["data"]
                  if trace["meta"].get("channel_id") == "ch0" and
                  trace["meta"].get("kind") is None)
    signal_click = {"points": [{
        "x": signal["x"][100], "y": signal["y"][100],
        "customdata": signal["customdata"][100],
    }]}
    valid_signal = plotly_ui.validate_native_graph_action(
        plotly_actions.normalize_action("signal-graph-click", signal_click),
        service.recording, service.workspace, state,
    )
    assert valid_signal is not None
    sidecar_bytes = service.repository.path.read_bytes()
    root_bytes = service.catalog_repository.path.read_bytes()
    prior_state = deepcopy(state)
    prior_figure = plotly_ui.signal_figure(service.recording, service.workspace, state)
    saves = []
    monkeypatch.setattr(service.repository, "save", lambda candidate: saves.append(candidate))

    updated, _, _ = plotly_actions.dispatch(
        valid_signal, state,
        active_channel_ids=tuple(channel.id for channel in service.recording.channels[:8]),
    )
    failed = plotly_ui.execute_graphical_marker_click(
        valid_signal, service, service.workspace, service.catalog, updated, prior_state,
    )
    assert failed[2] == prior_state
    assert failed[4] == {"kind": "graph-create-amplitude-marker-failed"}
    assert plotly_ui.signal_figure(service.recording, failed[0], failed[2]) == prior_figure
    assert saves == [] and service.repository.path.read_bytes() == sidecar_bytes
    assert service.catalog_repository.path.read_bytes() == root_bytes

    stale_state = {**state, "selected_amplitude_catalog_id": "stale"}
    updated, _, _ = plotly_actions.dispatch(
        valid_signal, stale_state,
        active_channel_ids=tuple(channel.id for channel in service.recording.channels[:8]),
    )
    failed = plotly_ui.execute_graphical_marker_click(
        valid_signal, service, service.workspace, service.catalog, updated, stale_state,
    )
    assert failed[2] == stale_state and saves == []

    point_trace = next(trace for trace in figure["data"]
                       if trace.get("meta", {}).get("annotation_id") == marker.id)
    interval_trace = next(trace for trace in figure["data"]
                          if trace.get("meta", {}).get("annotation_id") == band.id)
    for trace in (point_trace, interval_trace):
        click = {"points": [{
            "x": trace["x"][0], "y": trace["y"][0],
            "customdata": trace["customdata"][0],
        }]}
        action = plotly_ui.validate_native_graph_action(
            plotly_actions.normalize_action("signal-graph-click", click),
            service.recording, service.workspace, state,
        )
        channel_state, _, _ = plotly_actions.dispatch(
            action, state,
            active_channel_ids=tuple(channel.id for channel in service.recording.channels[:8]),
        )
        retained = plotly_ui.execute_graphical_marker_click(
            action, service, service.workspace, service.catalog, channel_state, state,
        )
        assert retained[2]["selection"] is None and retained[4] is None
        assert saves == [] and service.repository.path.read_bytes() == sidecar_bytes

    malformed = [
        {"points": [{"customdata": signal["customdata"][0]}]},
        {"points": [{"x": signal["x"][0], "customdata": signal["customdata"][0]},
                    {"x": signal["x"][1], "customdata": signal["customdata"][1]}]},
        {"points": [{"x": float("nan"), "customdata": signal["customdata"][0]}]},
        {"points": [{"x": signal["x"][0], "customdata": ["unknown", "ch0", "signal"]}]},
        {"points": [{"customdata": ["point/stale", "stale", "ch0", "point"]}]},
    ]
    for click in malformed:
        action = plotly_actions.normalize_action("signal-graph-click", click)
        validated = plotly_ui.validate_native_graph_action(
            action, service.recording, service.workspace, state,
        )
        assert validated is None
        retained, _, _ = plotly_actions.dispatch(
            validated, state,
            active_channel_ids=tuple(channel.id for channel in service.recording.channels[:8]),
        )
        assert retained == state
        assert plotly_ui.signal_figure(service.recording, service.workspace, retained) == prior_figure
        assert saves == [] and service.repository.path.read_bytes() == sidecar_bytes

    assert plotly_ui.validate_native_graph_action(
        valid_signal, service.recording, Workspace("wrong-recording"), state,
    ) is None
    assert service.catalog_repository.path.read_bytes() == root_bytes


def test_selected_marker_reassignment_and_delete_commit_once_and_preserve_fields(
        tmp_path, monkeypatch):
    service = _catalog_service(tmp_path)
    first = service.add_amplitude_label("Peak")
    second = service.add_amplitude_label("Valley")
    marker = service.add_catalog_marker("ch0", .4, first["id"])
    candidate = deepcopy(service.workspace)
    candidate.annotations[0] = replace(
        marker,
        label=replace(marker.label, attributes={
            **marker.label.attributes, "project:quality": "kept",
        }),
        extensions={"project:note": {"nested": True}},
    )
    service.repository.save(candidate)
    service.reload()
    before = service.workspace.annotations[0]
    state = _selected_point_state(service, before)
    state["selected_amplitude_catalog_id"] = second["id"]
    root_bytes = service.catalog_repository.path.read_bytes()
    saves = []
    original_save = service.repository.save

    def counted_save(workspace):
        saves.append(workspace)
        return original_save(workspace)

    monkeypatch.setattr(service.repository, "save", counted_save)
    action = plotly_actions.normalize_action(plotly_ui.REASSIGN_POINT_BUTTON_ID, 1)
    visible, catalog, retained, status, result = plotly_ui.execute_selected_marker_action(
        action, service, service.workspace, service.catalog, state,
    )
    reassigned = service.workspace.annotations[0]

    assert len(saves) == 1
    assert visible is service.workspace and catalog == service.catalog
    assert retained == state
    assert status == "Selected amplitude point relabeled."
    assert result == {"kind": "reassign-selected-point", "annotation_id": before.id}
    assert reassigned.id == before.id
    assert reassigned.location == before.location
    assert reassigned.provenance == before.provenance
    assert reassigned.extensions == before.extensions
    assert reassigned.label.code == before.label.code
    assert reassigned.label.text == second["name"]
    assert reassigned.label.attributes == {
        "project:quality": "kept",
        AMPLITUDE_LABEL_ID_ATTRIBUTE: second["id"],
        AMPLITUDE_LABEL_NAME_ATTRIBUTE: second["name"],
        AMPLITUDE_LABEL_COLOR_ATTRIBUTE: second["color"],
    }
    assert service.catalog_repository.path.read_bytes() == root_bytes

    delete = plotly_actions.normalize_action(
        plotly_ui.BRIDGE_ID, {"type": "delete-request", "key": "Delete"},
    )
    visible, catalog, cleared, status, result = plotly_ui.execute_selected_marker_action(
        delete, service, visible, catalog, retained,
    )
    assert len(saves) == 2
    assert service.workspace.annotations == []
    assert cleared["selection"] is None
    assert cleared["selected_visual"] == {"annotation_id": None, "kind": None}
    assert status == "Selected amplitude point deleted."
    assert result == {"kind": "delete-request", "annotation_id": before.id}
    assert service.catalog_repository.path.read_bytes() == root_bytes
    reopened = AnnotationService(
        service.recording,
        JsonSidecarRepository(service.repository.path, service.recording),
        "reopened",
        service.catalog_repository,
    )
    assert reopened.workspace == visible


def test_selected_marker_reassign_and_bridge_delete_use_real_dash_callback_once_each(
        tmp_path, monkeypatch):
    service = _catalog_service(tmp_path)
    first = service.add_amplitude_label("Peak")
    second = service.add_amplitude_label("Valley")
    marker = service.add_catalog_marker("ch0", .4, first["id"])
    state = _selected_point_state(service, marker)
    state["selected_amplitude_catalog_id"] = second["id"]
    app = plotly_ui.create_dash_app(service=service)
    callback_key, callback = next(
        (key, value) for key, value in app.callback_map.items()
        if "transient-state-store.data" in key
    )
    outputs = [
        {"id": output.component_id, "property": output.component_property}
        for output in callback["output"]
    ]

    def payload(changed, transient, bridge=None):
        overrides = {
            (plotly_ui.BRIDGE_ID, "data"): bridge,
            (plotly_ui.AMPLITUDE_SELECT_ID, "value"): second["id"],
            (plotly_ui.REASSIGN_POINT_BUTTON_ID, "n_clicks"): 1,
            (plotly_ui.STATE_ID, "data"): transient,
        }

        def item(spec):
            key = (spec["id"], spec["property"])
            component = _component_by_id(app.layout, spec["id"])
            return {
                "id": spec["id"],
                "property": spec["property"],
                "value": overrides.get(key, getattr(component, spec["property"], None)),
            }

        return {
            "output": callback_key,
            "outputs": outputs,
            "changedPropIds": [changed],
            "inputs": [item(spec) for spec in callback["inputs"]],
            "state": [item(spec) for spec in callback["state"]],
        }

    saves = []
    original_save = service.repository.save

    def counted_save(workspace):
        saves.append(workspace)
        return original_save(workspace)

    monkeypatch.setattr(service.repository, "save", counted_save)
    root_bytes = service.catalog_repository.path.read_bytes()
    client = app.server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=payload(f"{plotly_ui.REASSIGN_POINT_BUTTON_ID}.n_clicks", state),
    )
    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body[plotly_ui.DISPATCH_ID]["data"] == {
        "kind": "reassign-selected-point", "annotation_id": marker.id,
    }
    retained = body[plotly_ui.STATE_ID]["data"]
    assert retained["selection"] == state["selection"]
    assert service.workspace.annotations[0].label.text == second["name"]
    assert len(saves) == 1

    response = client.post(
        "/_dash-update-component",
        json=payload(
            f"{plotly_ui.BRIDGE_ID}.data", retained,
            {"type": "delete-request", "key": "Delete", "nonce": 1},
        ),
    )
    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body[plotly_ui.DISPATCH_ID]["data"] == {
        "kind": "delete-request", "annotation_id": marker.id,
    }
    assert body[plotly_ui.STATE_ID]["data"]["selection"] is None
    assert body["signal-annotation-app"]["data-has-selection"] == "false"
    assert service.workspace.annotations == [] and len(saves) == 2
    assert service.catalog_repository.path.read_bytes() == root_bytes


def test_selected_marker_mutations_reject_stale_tampered_and_protected_targets(
        tmp_path, monkeypatch):
    service = _catalog_service(tmp_path)
    amplitude = service.add_amplitude_label("Peak")
    marker = service.add_catalog_marker("ch0", .4, amplitude["id"])
    state = _selected_point_state(service, marker)
    state["selected_amplitude_catalog_id"] = amplitude["id"]
    sidecar_bytes = service.repository.path.read_bytes()
    root_bytes = service.catalog_repository.path.read_bytes()
    before_workspace = deepcopy(service.workspace)
    before_figure = plotly_ui.signal_figure(service.recording, service.workspace, state)
    saves = []
    monkeypatch.setattr(service.repository, "save", lambda candidate: saves.append(candidate))

    invalid_states = [
        {**state, "selection": None},
        {**state, "selection": [state["selection"], state["selection"]]},
        {**state, "selection": {**state["selection"], "extra": True}},
        {**state, "selection": {**state["selection"], "projection_id": "point/stale"}},
        {**state, "selection": {**state["selection"], "channel_id": "ch1"}},
        {**state, "selection": {**state["selection"], "kind": "interval"}},
        {**state, "selected_visual": {"annotation_id": "tampered", "kind": "point"}},
    ]
    actions = [
        {"kind": "reassign-selected-point"},
        {"kind": "delete-request"},
    ]
    for invalid in invalid_states:
        for action in actions:
            result = plotly_ui.execute_selected_marker_action(
                action, service, service.workspace, service.catalog, invalid,
            )
            assert result[0] is service.workspace and result[2] == invalid
            assert result[4]["kind"].endswith("-failed")
            assert service.workspace == before_workspace and saves == []
            assert service.repository.path.read_bytes() == sidecar_bytes
            assert service.catalog_repository.path.read_bytes() == root_bytes

    incompatible = plotly_ui.execute_selected_marker_action(
        actions[1], service, Workspace("wrong-recording"), service.catalog, state,
    )
    assert incompatible[2] == state and incompatible[4]["kind"] == "delete-request-failed"
    stale_label_state = {**state, "selected_amplitude_catalog_id": "stale"}
    stale_label = plotly_ui.execute_selected_marker_action(
        actions[0], service, service.workspace, service.catalog, stale_label_state,
    )
    assert stale_label[2] == stale_label_state
    assert stale_label[4]["kind"] == "reassign-selected-point-failed"
    assert saves == [] and service.repository.path.read_bytes() == sidecar_bytes
    assert plotly_ui.signal_figure(service.recording, service.workspace, state) == before_figure


def test_selected_marker_mutations_reject_relationship_model_and_save_failure(
        tmp_path, monkeypatch):
    service = _catalog_service(tmp_path)
    first = service.add_amplitude_label("Peak")
    second = service.add_amplitude_label("Valley")
    marker = service.add_catalog_marker("ch0", .4, first["id"])
    state = _selected_point_state(service, marker)
    state["selected_amplitude_catalog_id"] = second["id"]
    candidate = deepcopy(service.workspace)
    candidate.relationships.append(Relationship(
        "related", Reference("annotation", marker.id), Reference("channel", "ch1"),
        Label("near", "Near"),
    ))
    service.repository.save(candidate)
    service.reload()
    state = _selected_point_state(service, service.workspace.annotations[0])
    state["selected_amplitude_catalog_id"] = second["id"]
    sidecar_bytes = service.repository.path.read_bytes()
    root_bytes = service.catalog_repository.path.read_bytes()
    for action in ({"kind": "reassign-selected-point"}, {"kind": "delete-request"}):
        related = plotly_ui.execute_selected_marker_action(
            action, service, service.workspace, service.catalog, state,
        )
        assert related[4]["kind"].endswith("-failed")
        assert service.repository.path.read_bytes() == sidecar_bytes

    service.repository.save(Workspace(service.recording.id))
    service.reload()
    channel = service.recording.channel("ch0")
    index, seconds, value = channel.sample_at(.4)
    proposal = Proposal(
        "model-point", Location("point", ("ch0",), Coordinate(seconds, index),
                                amplitude=Amplitude(channel.units, value)),
        Label("marker", "Suggested peak"), "future-model", "v1", .9,
        "2026-10-05T00:00:00+00:00",
    )
    service.register_proposals([proposal])
    service.review_proposal(proposal.id, True)
    model = service.workspace.annotations[0]
    model_state = _selected_point_state(service, model)
    model_state["selected_amplitude_catalog_id"] = second["id"]
    model_bytes = service.repository.path.read_bytes()
    for action in ({"kind": "reassign-selected-point"}, {"kind": "delete-request"}):
        rejected = plotly_ui.execute_selected_marker_action(
            action, service, service.workspace, service.catalog, model_state,
        )
        assert rejected[4]["kind"].endswith("-failed")
        assert service.workspace.annotations == [model]
        assert service.repository.path.read_bytes() == model_bytes

    service.repository.save(Workspace(service.recording.id))
    service.reload()
    marker = service.add_catalog_marker("ch0", .4, first["id"])
    state = _selected_point_state(service, marker)
    state["selected_amplitude_catalog_id"] = second["id"]
    before_workspace = deepcopy(service.workspace)
    before_bytes = service.repository.path.read_bytes()

    attempts = []

    def denied(candidate):
        attempts.append(candidate)
        raise StorageError("injected save failure")

    monkeypatch.setattr(service.repository, "save", denied)
    for action in ({"kind": "reassign-selected-point"}, {"kind": "delete-request"}):
        before_attempts = len(attempts)
        rejected = plotly_ui.execute_selected_marker_action(
            action, service, service.workspace, service.catalog, state,
        )
        assert len(attempts) == before_attempts + 1
        assert rejected[2] == state and rejected[4]["kind"].endswith("-failed")
        assert service.workspace == before_workspace
        assert service.repository.path.read_bytes() == before_bytes
        assert service.catalog_repository.path.read_bytes() == root_bytes


def test_native_selected_point_relayout_normalizes_only_server_owned_x_variants():
    manifest = {
        "0": {
            "kind": "point-time-handle", "role": "point",
            "projection_id": "point/id/channel/ch0",
            "annotation_id": "id", "channel_id": "ch0",
        }
    }
    variants = (
        ({"shapes[0].x0": .40}, .40),
        ({"shapes[0]": {"x0": .40, "x1": .42, "y0": -99, "y1": 99}}, .41),
        ({"shapes": [{"x0": .42, "x1": .42, "y0": -7, "y1": 7}]}, .42),
    )
    for payload, expected in variants:
        action = plotly_actions.normalize_selected_point_relayout(payload, manifest)
        assert action["kind"] == "move-selected-point"
        assert action["role"] == "point"
        assert action["projection_id"] == "point/id/channel/ch0"
        assert action["annotation_id"] == "id"
        assert action["channel_id"] == "ch0"
        assert action["candidate_time"] == pytest.approx(expected)
        assert set(action["x_fields"]).issubset({"x0", "x1"})

    assert plotly_actions.normalize_selected_point_relayout(
        {"shapes[0].y0": -100, "shapes[0].y1": 100}, manifest,
    ) is None
    assert plotly_actions.normalize_selected_point_relayout(
        {"xaxis.range": [0, 1]}, manifest,
    ) is None
    assert plotly_actions.normalize_selected_point_relayout(
        {"shapes[9].x0": .5}, manifest,
    ) is None
    malformed = plotly_actions.normalize_selected_point_relayout(
        {"shapes[0].x0": float("inf")}, manifest,
    )
    assert malformed["candidate_time"] is None


def test_native_selected_marker_move_commits_once_snaps_and_reopens(tmp_path, monkeypatch):
    service = _catalog_service(tmp_path)
    amplitude = service.add_amplitude_label("Peak")
    marker = service.add_catalog_marker("ch0", .4, amplitude["id"])
    candidate = deepcopy(service.workspace)
    candidate.annotations[0] = replace(
        marker, extensions={"project:note": {"kept": True}},
    )
    service.repository.save(candidate)
    service.reload()
    before = service.workspace.annotations[0]
    state = _selected_point_state(service, before)
    manifest = plotly_ui.selected_marker_shape_manifest(
        service.recording, service.workspace, state,
    )
    action = plotly_actions.normalize_selected_point_relayout(
        {"shapes[0]": {"x0": .4011, "x1": .4011, "y0": -99, "y1": 99}},
        manifest,
    )
    sidecar_before = service.repository.path.read_bytes()
    root_before = service.catalog_repository.path.read_bytes()
    saves = []
    original_save = service.repository.save

    def counted_save(workspace):
        saves.append(workspace)
        return original_save(workspace)

    monkeypatch.setattr(service.repository, "save", counted_save)
    visible, catalog, retained, status, result = plotly_ui.execute_selected_marker_move(
        action, service, service.workspace, service.catalog, state,
    )
    moved = service.workspace.annotations[0]

    assert len(saves) == 1 and service.repository.path.read_bytes() != sidecar_before
    assert service.catalog_repository.path.read_bytes() == root_before
    assert visible is service.workspace and catalog == service.catalog
    assert retained == state
    assert status == "Selected amplitude point moved."
    assert result == {
        "kind": "move-selected-point", "annotation_id": before.id, "time": .402,
    }
    assert moved.id == before.id
    assert moved.location.start.seconds == .402
    assert moved.location.start.sample_index == 201
    assert moved.location.amplitude.value == service.recording.channel("ch0").values[201]
    assert moved.label == before.label
    assert moved.provenance == before.provenance
    assert moved.extensions == before.extensions

    figure = plotly_ui.signal_figure(service.recording, visible, retained)
    point = next(trace for trace in figure["data"]
                 if trace.get("meta", {}).get("annotation_id") == before.id)
    handle = figure["layout"]["shapes"][0]
    assert point["x"] == [.402]
    assert handle["x0"] == handle["x1"] == .402
    assert handle["y0"] == 0 and handle["y1"] == 1
    assert handle["editable"] is True

    reopened = AnnotationService(
        service.recording,
        JsonSidecarRepository(service.repository.path, service.recording),
        "reopened",
        service.catalog_repository,
    )
    assert reopened.workspace == service.workspace


def test_native_marker_move_rejects_invalid_identity_state_and_geometry_without_writes(
        tmp_path, monkeypatch):
    service = _catalog_service(tmp_path)
    amplitude = service.add_amplitude_label("Peak")
    marker = service.add_catalog_marker("ch0", .4, amplitude["id"])
    state = _selected_point_state(service, marker)
    manifest = plotly_ui.selected_marker_shape_manifest(
        service.recording, service.workspace, state,
    )
    valid = plotly_actions.normalize_selected_point_relayout(
        {"shapes[0].x0": .6, "shapes[0].x1": .6}, manifest,
    )
    before_workspace = deepcopy(service.workspace)
    before_figure = plotly_ui.signal_figure(service.recording, service.workspace, state)
    sidecar_before = service.repository.path.read_bytes()
    root_before = service.catalog_repository.path.read_bytes()
    saves = []
    monkeypatch.setattr(service.repository, "save", lambda workspace: saves.append(workspace))

    invalid = [
        (valid, {**state, "mode": "mark-amplitude"}, service.workspace),
        (valid, {**state, "selection": None}, service.workspace),
        (valid, {**state, "selection": [state["selection"], state["selection"]]}, service.workspace),
        (valid, {**state, "selection": {**state["selection"], "projection_id": "stale"}}, service.workspace),
        (valid, {**state, "selected_visual": {"annotation_id": "tampered", "kind": "point"}}, service.workspace),
        ({**valid, "annotation_id": "tampered"}, state, service.workspace),
        ({**valid, "channel_id": "ch1"}, state, service.workspace),
        ({**valid, "role": "end"}, state, service.workspace),
        ({**valid, "candidate_time": None}, state, service.workspace),
        ({**valid, "candidate_time": float("nan")}, state, service.workspace),
        ({**valid, "candidate_time": -1.0}, state, service.workspace),
        ({**valid, "candidate_time": service.recording.channel("ch0").duration + 1}, state, service.workspace),
        (valid, state, Workspace("wrong-recording")),
    ]
    for action, invalid_state, visible_workspace in invalid:
        result = plotly_ui.execute_selected_marker_move(
            action, service, visible_workspace, service.catalog, invalid_state,
        )
        assert result[0] is visible_workspace
        assert result[2] == invalid_state
        assert result[4]["kind"] == "move-selected-point-failed"
        assert service.workspace == before_workspace
        assert service.repository.path.read_bytes() == sidecar_before
        assert service.catalog_repository.path.read_bytes() == root_before
        assert plotly_ui.signal_figure(
            service.recording, service.workspace, state,
        ) == before_figure
        assert saves == []

    unavailable = plotly_ui.execute_selected_marker_move(
        valid, None, service.workspace, service.catalog, state,
    )
    assert unavailable[4]["kind"] == "move-selected-point-unavailable"


def test_native_marker_move_real_dash_callback_persists_once_and_redraws_canonically(
        tmp_path, monkeypatch):
    service = _catalog_service(tmp_path)
    amplitude = service.add_amplitude_label("Peak")
    marker = service.add_catalog_marker("ch0", .4, amplitude["id"])
    state = _selected_point_state(service, marker)
    app = plotly_ui.create_dash_app(service=service)
    callback_key, callback = next(
        (key, value) for key, value in app.callback_map.items()
        if "transient-state-store.data" in key
    )
    outputs = [
        {"id": output.component_id, "property": output.component_property}
        for output in callback["output"]
    ]
    relayout = {
        "shapes[0].x0": .611,
        "shapes[0].x1": .611,
        "shapes[0].y0": -100,
        "shapes[0].y1": 100,
    }
    overrides = {
        (plotly_ui.GRAPH_ID, "relayoutData"): relayout,
        (plotly_ui.STATE_ID, "data"): state,
    }

    def item(spec):
        key = (spec["id"], spec["property"])
        component = _component_by_id(app.layout, spec["id"])
        return {
            "id": spec["id"], "property": spec["property"],
            "value": overrides.get(key, getattr(component, spec["property"], None)),
        }

    payload = {
        "output": callback_key,
        "outputs": outputs,
        "changedPropIds": [f"{plotly_ui.GRAPH_ID}.relayoutData"],
        "inputs": [item(spec) for spec in callback["inputs"]],
        "state": [item(spec) for spec in callback["state"]],
    }
    saves = []
    original_save = service.repository.save

    def counted_save(workspace):
        saves.append(workspace)
        return original_save(workspace)

    monkeypatch.setattr(service.repository, "save", counted_save)
    root_before = service.catalog_repository.path.read_bytes()
    response = app.server.test_client().post("/_dash-update-component", json=payload)

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert len(saves) == 1
    assert body[plotly_ui.DISPATCH_ID]["data"] == {
        "kind": "move-selected-point", "annotation_id": marker.id, "time": .612,
    }
    assert body[plotly_ui.STATE_ID]["data"] == state
    shape = body[plotly_ui.GRAPH_ID]["figure"]["layout"]["shapes"][0]
    assert shape["x0"] == shape["x1"] == .612
    assert shape["y0"] == 0 and shape["y1"] == 1
    assert service.workspace.annotations[0].location.start.seconds == .612
    assert service.catalog_repository.path.read_bytes() == root_before


def test_native_marker_move_protected_targets_and_save_failure_roll_back(tmp_path, monkeypatch):
    service = _catalog_service(tmp_path)
    amplitude = service.add_amplitude_label("Peak")
    marker = service.add_catalog_marker("ch0", .4, amplitude["id"])
    state = _selected_point_state(service, marker)
    selection = state["selection"]
    action = {
        "kind": "move-selected-point", "role": "point",
        "projection_id": selection["projection_id"],
        "annotation_id": selection["annotation_id"],
        "channel_id": selection["channel_id"],
        "candidate_time": .8, "x_fields": {"x0": .8},
    }
    relation = Relationship(
        "related", Reference("annotation", marker.id), Reference("channel", "ch1"),
        Label("near", "Near"),
    )
    related = deepcopy(service.workspace)
    related.relationships.append(relation)
    service.repository.save(related)
    service.reload()
    related_state = _selected_point_state(service, service.workspace.annotations[0])
    before_bytes = service.repository.path.read_bytes()
    rejected = plotly_ui.execute_selected_marker_move(
        action, service, service.workspace, service.catalog, related_state,
    )
    assert rejected[4]["kind"] == "move-selected-point-failed"
    assert service.repository.path.read_bytes() == before_bytes
    assert plotly_ui.selected_marker_shape_manifest(
        service.recording, service.workspace, related_state,
    ) == {}

    service.repository.save(Workspace(service.recording.id))
    service.reload()
    legacy = service.add("maximum", "ch0", .4, None, "Maximum")
    legacy_state = _selected_point_state(service, legacy)
    legacy_selection = legacy_state["selection"]
    legacy_action = {
        **action,
        "projection_id": legacy_selection["projection_id"],
        "annotation_id": legacy.id,
        "channel_id": legacy.channel_id,
    }
    legacy_bytes = service.repository.path.read_bytes()
    rejected = plotly_ui.execute_selected_marker_move(
        legacy_action, service, service.workspace, service.catalog, legacy_state,
    )
    assert rejected[4]["kind"] == "move-selected-point-failed"
    assert service.repository.path.read_bytes() == legacy_bytes
    assert plotly_ui.selected_marker_shape_manifest(
        service.recording, service.workspace, legacy_state,
    ) == {}

    service.repository.save(Workspace(service.recording.id))
    service.reload()
    channel = service.recording.channel("ch0")
    sample_index, seconds, value = channel.sample_at(.4)
    proposal = Proposal(
        "model-point", Location(
            "point", ("ch0",), Coordinate(seconds, sample_index),
            amplitude=Amplitude(channel.units, value),
        ),
        Label("marker", "Suggested peak"), "future-model", "v1", .9,
        "2026-10-05T00:00:00+00:00",
    )
    service.register_proposals([proposal])
    service.review_proposal(proposal.id, True)
    model = service.workspace.annotations[0]
    model_state = _selected_point_state(service, model)
    model_selection = model_state["selection"]
    model_action = {
        **action,
        "projection_id": model_selection["projection_id"],
        "annotation_id": model.id,
        "channel_id": model.channel_id,
    }
    model_bytes = service.repository.path.read_bytes()
    rejected = plotly_ui.execute_selected_marker_move(
        model_action, service, service.workspace, service.catalog, model_state,
    )
    assert rejected[4]["kind"] == "move-selected-point-failed"
    assert service.repository.path.read_bytes() == model_bytes
    assert plotly_ui.selected_marker_shape_manifest(
        service.recording, service.workspace, model_state,
    ) == {}

    clean = Workspace(service.recording.id, annotations=[marker])
    service.repository.save(clean)
    service.reload()
    state = _selected_point_state(service, marker)
    before_workspace = deepcopy(service.workspace)
    before_bytes = service.repository.path.read_bytes()
    attempts = []

    def denied(candidate):
        attempts.append(candidate)
        raise storage.StorageError("injected save failure")

    monkeypatch.setattr(service.repository, "save", denied)
    rejected = plotly_ui.execute_selected_marker_move(
        action, service, service.workspace, service.catalog, state,
    )
    assert len(attempts) == 1
    assert rejected[2] == state
    assert rejected[4]["kind"] == "move-selected-point-failed"
    assert service.workspace == before_workspace
    assert service.repository.path.read_bytes() == before_bytes

    bridge_source = files("signal_annotation").joinpath("assets/plotly_events.js").read_text()
    for forbidden in (
            "Plotly.relayout", "getBoundingClientRect", "drag(", ".style."):
        assert forbidden not in bridge_source
