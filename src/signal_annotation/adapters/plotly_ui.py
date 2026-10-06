"""Dash application shell with signal-only Plotly navigation."""

from __future__ import annotations

from math import isfinite
from pathlib import Path

from dash import Dash, Input, Output, State, callback_context, dcc, html, no_update

from .plotly_actions import (
    DEFAULT_MODE, dispatch, initial_transient_state, normalize_action,
    normalize_selected_point_relayout,
)
from .plotly_presenter import bounded_viewport, build_presentation, build_signal_figure
from .storage import export_annotations_csv
from ..domain import Recording, ValidationError, Workspace
from ..label_catalog import LabelCatalog, MAX_LABELS_PER_COLLECTION
from ..validation import GENERAL_NOTES_KEY


ASSETS_DIRECTORY = Path(__file__).resolve().parents[1] / "assets"
GRAPH_ID = "signal-graph"
MODE_ID = "annotation-mode-control"
STATUS_ID = "status-region"
STATE_ID = "transient-state-store"
BRIDGE_ID = "bridge-event-store"
DISPATCH_ID = "dispatcher-result-store"
HELP_DIALOG_ID = "help-dialog"
HELP_OPEN_ID = "open-help-button"
HELP_CLOSE_ID = "close-help-button"
START_INPUT_ID = "time-window-start-input"
WIDTH_INPUT_ID = "time-window-width-input"
GO_BUTTON_ID = "go-time-window-button"
FULL_VIEW_BUTTON_ID = "full-view-button"
RELOAD_BUTTON_ID = "reload-saved-state-button"
AMPLITUDE_NAME_INPUT_ID = "amplitude-label-name-input"
AMPLITUDE_ADD_BUTTON_ID = "add-amplitude-label-button"
AMPLITUDE_SELECT_ID = "amplitude-label-select"
AMPLITUDE_STATUS_ID = "amplitude-label-status"
REASSIGN_POINT_BUTTON_ID = "reassign-selected-point-button"
INTERVAL_NAME_INPUT_ID = "interval-label-name-input"
INTERVAL_ADD_BUTTON_ID = "add-interval-label-button"
INTERVAL_SELECT_ID = "interval-label-select"
INTERVAL_STATUS_ID = "interval-label-status"
GENERAL_NOTES_ID = "general-notes-input"
SAVE_NOTES_BUTTON_ID = "save-general-notes-button"
CLEAR_NOTES_BUTTON_ID = "clear-general-notes-button"
EXPORT_CSV_BUTTON_ID = "export-csv-button"
ACTIVE_CHANNEL_ID = "active-channel-context"
POINT_TIME_INPUT_ID = "point-time-input"
CREATE_POINT_BUTTON_ID = "create-amplitude-marker-button"
INTERVAL_START_INPUT_ID = "interval-start-input"
INTERVAL_END_INPUT_ID = "interval-end-input"
CREATE_INTERVAL_BUTTON_ID = "create-interval-button"

COMPONENT_IDS = (
    "signal-annotation-app",
    GRAPH_ID,
    MODE_ID,
    STATUS_ID,
    STATE_ID,
    BRIDGE_ID,
    DISPATCH_ID,
    HELP_DIALOG_ID,
    HELP_OPEN_ID,
    HELP_CLOSE_ID,
    START_INPUT_ID,
    WIDTH_INPUT_ID,
    GO_BUTTON_ID,
    FULL_VIEW_BUTTON_ID,
    RELOAD_BUTTON_ID,
    AMPLITUDE_NAME_INPUT_ID,
    AMPLITUDE_ADD_BUTTON_ID,
    AMPLITUDE_SELECT_ID,
    AMPLITUDE_STATUS_ID,
    REASSIGN_POINT_BUTTON_ID,
    INTERVAL_NAME_INPUT_ID,
    INTERVAL_ADD_BUTTON_ID,
    INTERVAL_SELECT_ID,
    INTERVAL_STATUS_ID,
    GENERAL_NOTES_ID,
    SAVE_NOTES_BUTTON_ID,
    CLEAR_NOTES_BUTTON_ID,
    EXPORT_CSV_BUTTON_ID,
    ACTIVE_CHANNEL_ID,
    POINT_TIME_INPUT_ID,
    CREATE_POINT_BUTTON_ID,
    INTERVAL_START_INPUT_ID,
    INTERVAL_END_INPUT_ID,
    CREATE_INTERVAL_BUTTON_ID,
)


def empty_figure() -> dict[str, object]:
    """Return a valid blank figure when no recording is injected."""

    return {"data": [], "layout": {"template": "plotly_white", "uirevision": "shell"}}


def _initial_state(recording: Recording | None) -> dict[str, object]:
    state = initial_transient_state()
    if recording is not None:
        state["viewport"] = bounded_viewport(recording)
        state["active_channel"] = recording.channels[0].id
    return state


def signal_figure(recording: Recording | None, workspace: Workspace | None,
                  state: object) -> dict[str, object]:
    """Project the injected recording and workspace for one bounded viewport."""

    if recording is None:
        return empty_figure()
    current = state if isinstance(state, dict) else {}
    viewport = current.get("viewport")
    if not isinstance(viewport, dict):
        viewport = bounded_viewport(recording)
    presentation = build_presentation(
        recording,
        workspace if workspace is not None else Workspace(recording.id),
        start=viewport.get("start"),
        end=viewport.get("end"),
    )
    return build_signal_figure(presentation, state)


def reload_visible_workspace(service: object, visible_workspace: Workspace) -> tuple[Workspace, str]:
    """Use the existing validated service reload without replacing UI state on error."""
    try:
        service.reload()
    except ValidationError as exc:
        return visible_workspace, f"Reload failed: {exc}"
    return service.workspace, "Reloaded saved annotations."


def catalog_options(catalog: LabelCatalog, collection: str) -> list[dict[str, str]]:
    """Return visible color-labelled choices whose values are stable catalog IDs."""

    return [
        {"label": f"{entry.name} — {entry.color}", "value": entry.id}
        for entry in catalog.collection(collection)
    ]


def catalog_status(catalog: LabelCatalog, collection: str) -> str:
    return f"{len(catalog.collection(collection))}/{MAX_LABELS_PER_COLLECTION} labels"


def visible_notes(workspace: Workspace | None) -> str:
    """Read validated general notes without changing absent metadata."""

    return workspace.metadata.get(GENERAL_NOTES_KEY, "") if workspace is not None else ""


def active_channel_text(recording: Recording | None, state: object) -> str:
    """Describe the transient creation context without adding a channel selector."""

    channel_id = state.get("active_channel") if isinstance(state, dict) else None
    if recording is not None:
        for channel in recording.channels[:8]:
            if channel.id == channel_id:
                return f"Active channel: {channel.name} ({channel.units})"
    return "Active channel: unavailable"


def _service_catalog(service: object | None) -> LabelCatalog:
    catalog = getattr(service, "catalog", None) if service is not None else None
    return catalog if isinstance(catalog, LabelCatalog) else LabelCatalog()


def _reconcile_catalog_selections(state: dict[str, object], catalog: LabelCatalog) -> dict[str, object]:
    """Drop only selections that disappeared during a successful Reload."""

    next_state = dict(state)
    available = {
        "selected_amplitude_catalog_id": {entry.id for entry in catalog.amplitude_labels},
        "selected_interval_catalog_id": {entry.id for entry in catalog.interval_labels},
    }
    for key, values in available.items():
        if next_state.get(key) not in values:
            next_state[key] = None
    return next_state


def execute_durable_action(action: dict[str, object] | None, service: object | None,
                           visible_workspace: Workspace | None, visible_catalog: LabelCatalog,
                           state: dict[str, object], *, amplitude_name: object = "",
                           interval_name: object = "", notes: object = "") -> tuple[Workspace | None, LabelCatalog, dict[str, object], str, dict[str, object], object, object, object]:
    """Run one expected durable command while retaining all visible state on failure.

    The Dash callback remains the sole caller/owner of this boundary.  The helper is
    deliberately small so temporary-root tests can exercise service-backed rollback
    without a server or browser.
    """

    if action is None:
        return visible_workspace, visible_catalog, state, "Ready.", {"kind": "idle"}, notes, amplitude_name, interval_name
    kind = action["kind"]
    if kind not in {"add-amplitude-label", "add-interval-label", "save-general-notes",
                    "clear-general-notes", "export-csv", "reload"}:
        return visible_workspace, visible_catalog, state, "", {"kind": kind}, notes, amplitude_name, interval_name
    if service is None:
        return visible_workspace, visible_catalog, state, "This action requires an annotation service.", {"kind": f"{kind}-unavailable"}, notes, amplitude_name, interval_name
    try:
        if kind == "add-amplitude-label":
            if getattr(service, "catalog_repository", None) is None:
                raise ValidationError("Shared label catalogs are unavailable.")
            added = service.add_amplitude_label(action.get("name"))
            next_state = dict(state)
            next_state["selected_amplitude_catalog_id"] = added["id"]
            return service.workspace, _service_catalog(service), next_state, "Amplitude label added.", {"kind": kind}, notes, "", interval_name
        if kind == "add-interval-label":
            if getattr(service, "catalog_repository", None) is None:
                raise ValidationError("Shared label catalogs are unavailable.")
            added = service.add_interval_label(action.get("name"))
            next_state = dict(state)
            next_state["selected_interval_catalog_id"] = added["id"]
            return service.workspace, _service_catalog(service), next_state, "Interval label added.", {"kind": kind}, notes, amplitude_name, ""
        if kind == "save-general-notes":
            service.save_general_notes(action.get("notes"))
            return service.workspace, visible_catalog, state, "General notes saved.", {"kind": kind}, visible_notes(service.workspace), amplitude_name, interval_name
        if kind == "clear-general-notes":
            service.save_general_notes("")
            return service.workspace, visible_catalog, state, "General notes cleared.", {"kind": kind}, "", amplitude_name, interval_name
        if kind == "export-csv":
            path = service.repository.path.with_suffix(".csv")
            export_annotations_csv(service.workspace, service.recording, path)
            return visible_workspace, visible_catalog, state, f"Current CSV exported to: {path}. It will be replaced on the next export.", {"kind": kind, "path": str(path)}, notes, amplitude_name, interval_name
        service.reload()
        reloaded_catalog = _service_catalog(service)
        next_state = _reconcile_catalog_selections(state, reloaded_catalog)
        return service.workspace, reloaded_catalog, next_state, "Reloaded saved annotations.", {"kind": kind}, visible_notes(service.workspace), amplitude_name, interval_name
    except ValidationError as exc:
        return visible_workspace, visible_catalog, state, f"{kind.replace('-', ' ').capitalize()} failed: {exc}", {"kind": f"{kind}-failed"}, notes, amplitude_name, interval_name


def execute_numeric_creation(action: dict[str, object] | None, service: object | None,
                             visible_workspace: Workspace | None, visible_catalog: LabelCatalog,
                             state: dict[str, object]) -> tuple[Workspace | None, LabelCatalog, dict[str, object], str, dict[str, object]]:
    """Create one catalog-linked annotation while preserving all visible state on error."""

    if action is None or action.get("kind") not in {"create-amplitude-marker", "create-interval"}:
        return visible_workspace, visible_catalog, state, "Ready.", {"kind": "idle"}
    kind = action["kind"]
    if service is None:
        return visible_workspace, visible_catalog, state, "This action requires an annotation service.", {"kind": f"{kind}-unavailable"}
    if getattr(service, "catalog_repository", None) is None:
        return visible_workspace, visible_catalog, state, "Shared label catalogs are unavailable.", {"kind": f"{kind}-failed"}
    channel_id = state.get("active_channel")
    if not isinstance(channel_id, str) or channel_id not in {channel.id for channel in service.recording.channels[:8]}:
        return visible_workspace, visible_catalog, state, "Choose a valid active channel.", {"kind": f"{kind}-failed"}
    try:
        if kind == "create-amplitude-marker":
            if state.get("mode") != "mark-amplitude":
                raise ValidationError("Choose Mark amplitude mode before creating a point.")
            service.add_catalog_marker(channel_id, action.get("time"),
                                       state.get("selected_amplitude_catalog_id"))
            status = "Amplitude point created."
        else:
            if state.get("mode") != "mark-interval":
                raise ValidationError("Choose Mark interval mode before creating an interval.")
            service.add_catalog_interval(channel_id, action.get("start"), action.get("end"),
                                         state.get("selected_interval_catalog_id"))
            status = "Interval created."
    except ValidationError as exc:
        return visible_workspace, visible_catalog, state, f"{kind.replace('-', ' ').capitalize()} failed: {exc}", {"kind": f"{kind}-failed"}
    next_state = dict(state)
    next_state["selection"] = None
    next_state["selected_visual"] = {"annotation_id": None, "kind": None}
    return service.workspace, _service_catalog(service), next_state, status, {"kind": kind}


def validate_native_graph_action(action: dict[str, object] | None,
                                 recording: Recording | None,
                                 visible_workspace: Workspace | None,
                                 state: object) -> dict[str, object] | None:
    """Accept only an identity that exists in the current server-owned projection."""

    if (recording is None or not isinstance(action, dict) or
            action.get("kind") != "set-active-channel"):
        return None
    if (visible_workspace is not None and
            visible_workspace.recording_id != recording.id):
        return None
    target = action.get("graph_target")
    if target not in {"signal", "point", "interval"}:
        return None
    current = state if isinstance(state, dict) else {}
    viewport = current.get("viewport")
    if not isinstance(viewport, dict):
        viewport = bounded_viewport(recording)
    presentation = build_presentation(
        recording,
        visible_workspace if visible_workspace is not None else Workspace(recording.id),
        start=viewport.get("start"),
        end=viewport.get("end"),
    )
    projection_id = action.get("projection_id")
    channel_id = action.get("channel_id")
    if not isinstance(projection_id, str) or not isinstance(channel_id, str):
        return None
    if target == "signal":
        identity = presentation["manifest"]["channels"].get(projection_id)
        if identity is None or identity.get("channel_id") != channel_id:
            return None
        try:
            seconds = float(action.get("time"))
        except (TypeError, ValueError, OverflowError):
            return None
        channel = next((item for item in presentation["channels"]
                        if item["projection_id"] == projection_id), None)
        if channel is None or not any(
                sample_time == seconds and sample_value is not None
                for sample_time, sample_value in zip(channel["x"], channel["y"])):
            return None
        return dict(action)

    collection = "points" if target == "point" else "intervals"
    identity = presentation["manifest"][collection].get(projection_id)
    if (identity is None or identity.get("channel_id") != channel_id or
            identity.get("annotation_id") != action.get("annotation_id")):
        return None
    return dict(action)


def execute_graphical_marker_click(
        action: dict[str, object], service: object | None,
        visible_workspace: Workspace | None, visible_catalog: LabelCatalog,
        state: dict[str, object], prior_state: object,
) -> tuple[Workspace | None, LabelCatalog, dict[str, object], str | None,
           dict[str, object] | None]:
    """Create or select one point from an already validated native graph click."""

    target = action.get("graph_target")
    if target == "signal" and state.get("mode") == "mark-amplitude":
        workspace, catalog, created_state, status, result = execute_numeric_creation(
            {"kind": "create-amplitude-marker", "time": action.get("time")},
            service, visible_workspace, visible_catalog, state,
        )
        if result.get("kind") != "create-amplitude-marker":
            retained = (dict(prior_state) if isinstance(prior_state, dict)
                        else initial_transient_state())
            return visible_workspace, visible_catalog, retained, status, {
                "kind": "graph-create-amplitude-marker-failed",
            }
        return workspace, catalog, created_state, status, {
            "kind": "graph-create-amplitude-marker",
        }

    if target == "point" and state.get("mode") == "navigate":
        annotation_id = action.get("annotation_id")
        channel_id = action.get("channel_id")
        if visible_workspace is None or not any(
                annotation.id == annotation_id and
                annotation.location.type == "point" and
                annotation.channel_id == channel_id
                for annotation in visible_workspace.annotations):
            retained = (dict(prior_state) if isinstance(prior_state, dict)
                        else initial_transient_state())
            return visible_workspace, visible_catalog, retained, "Ignored a stale point target.", {
                "kind": "graph-select-point-failed",
            }
        next_state = dict(state)
        selection = {
            "projection_id": action["projection_id"],
            "annotation_id": annotation_id,
            "channel_id": channel_id,
            "kind": "point",
        }
        next_state["selection"] = selection
        next_state["selected_visual"] = {
            "annotation_id": annotation_id,
            "kind": "point",
        }
        return visible_workspace, visible_catalog, next_state, "Amplitude point selected.", {
            "kind": "select-point",
            "annotation_id": annotation_id,
        }

    return visible_workspace, visible_catalog, state, None, None


def _selected_current_amplitude_marker(service: object,
                                       visible_workspace: Workspace | None,
                                       state: object):
    """Resolve one browser selection through the current server projection."""

    if (visible_workspace is None or visible_workspace != service.workspace or
            visible_workspace.recording_id != service.recording.id):
        raise ValidationError("Reload the current recording before changing a point.")
    if not isinstance(state, dict):
        raise ValidationError("Select exactly one amplitude point.")
    selection = state.get("selection")
    if (not isinstance(selection, dict) or
            set(selection) != {"projection_id", "annotation_id", "channel_id", "kind"} or
            selection.get("kind") != "point"):
        raise ValidationError("Select exactly one amplitude point.")
    selected_visual = state.get("selected_visual")
    if selected_visual != {
            "annotation_id": selection.get("annotation_id"), "kind": "point"}:
        raise ValidationError("Select exactly one amplitude point.")

    viewport = state.get("viewport")
    if not isinstance(viewport, dict):
        viewport = bounded_viewport(service.recording)
    presentation = build_presentation(
        service.recording,
        service.workspace,
        start=viewport.get("start"),
        end=viewport.get("end"),
    )
    projected = presentation["manifest"]["points"].get(selection.get("projection_id"))
    if projected != {
            "recording_id": service.recording.id,
            "channel_id": selection.get("channel_id"),
            "annotation_id": selection.get("annotation_id"),
    }:
        raise ValidationError("The selected amplitude point is no longer current.")
    matches = [annotation for annotation in service.workspace.annotations
               if annotation.id == selection.get("annotation_id")]
    if len(matches) != 1:
        raise ValidationError("The selected amplitude point is no longer current.")
    annotation = matches[0]
    if (annotation.location.type != "point" or
            annotation.channel_id != selection.get("channel_id") or
            annotation.label.code != "marker" or annotation.origin != "manual"):
        raise ValidationError("Only one manual amplitude marker can be changed.")
    if any(
            relationship.source.type == "annotation" and
            relationship.source.id == annotation.id or
            relationship.target.type == "annotation" and
            relationship.target.id == annotation.id
            for relationship in service.workspace.relationships):
        raise ValidationError(
            "The amplitude marker has relationships and cannot be changed here."
        )
    return annotation


def execute_selected_marker_action(
        action: dict[str, object] | None, service: object | None,
        visible_workspace: Workspace | None, visible_catalog: LabelCatalog,
        state: dict[str, object],
) -> tuple[Workspace | None, LabelCatalog, dict[str, object], str,
           dict[str, object]]:
    """Reassign or delete one revalidated manual amplitude marker."""

    if action is None or action.get("kind") not in {
            "reassign-selected-point", "delete-request"}:
        return visible_workspace, visible_catalog, state, "Ready.", {"kind": "idle"}
    kind = action["kind"]
    if service is None:
        return (visible_workspace, visible_catalog, state,
                "This action requires an annotation service.",
                {"kind": f"{kind}-unavailable"})
    try:
        annotation = _selected_current_amplitude_marker(
            service, visible_workspace, state,
        )
        if kind == "reassign-selected-point":
            if getattr(service, "catalog_repository", None) is None:
                raise ValidationError("Shared label catalogs are unavailable.")
            service.reassign_point_label(
                annotation.id, state.get("selected_amplitude_catalog_id"),
            )
            return (service.workspace, _service_catalog(service), state,
                    "Selected amplitude point relabeled.",
                    {"kind": kind, "annotation_id": annotation.id})
        service.delete([annotation.id])
    except ValidationError as exc:
        return (visible_workspace, visible_catalog, state,
                f"{kind.replace('-', ' ').capitalize()} failed: {exc}",
                {"kind": f"{kind}-failed"})

    next_state = dict(state)
    next_state["selection"] = None
    next_state["selected_visual"] = {"annotation_id": None, "kind": None}
    return (service.workspace, _service_catalog(service), next_state,
            "Selected amplitude point deleted.",
            {"kind": kind, "annotation_id": annotation.id})


def selected_marker_shape_manifest(recording: Recording | None,
                                   visible_workspace: Workspace | None,
                                   state: object) -> dict[str, object]:
    """Read editable-shape identity only from the canonical server figure."""

    figure = signal_figure(recording, visible_workspace, state)
    layout = figure.get("layout")
    meta = layout.get("meta") if isinstance(layout, dict) else None
    manifest = meta.get("shape_by_index") if isinstance(meta, dict) else None
    return manifest if isinstance(manifest, dict) else {}


def execute_selected_marker_move(
        action: dict[str, object] | None, service: object | None,
        visible_workspace: Workspace | None, visible_catalog: LabelCatalog,
        state: dict[str, object],
) -> tuple[Workspace | None, LabelCatalog, dict[str, object], str,
           dict[str, object]]:
    """Move one current selected manual marker from a native x-only proposal."""

    if action is None or action.get("kind") != "move-selected-point":
        return visible_workspace, visible_catalog, state, "Ready.", {"kind": "idle"}
    kind = action["kind"]
    if service is None:
        return (visible_workspace, visible_catalog, state,
                "This action requires an annotation service.",
                {"kind": f"{kind}-unavailable"})
    try:
        if state.get("mode") != "navigate":
            raise ValidationError("Move amplitude markers only in Navigate mode.")
        if set(action) != {
                "kind", "role", "projection_id", "annotation_id", "channel_id",
                "candidate_time", "x_fields"} or action.get("role") != "point":
            raise ValidationError("The native point handle is invalid.")
        annotation = _selected_current_amplitude_marker(
            service, visible_workspace, state,
        )
        selection = state["selection"]
        if any(action.get(key) != selection.get(key) for key in (
                "projection_id", "annotation_id", "channel_id")):
            raise ValidationError("The native point handle is no longer current.")
        candidate = action.get("candidate_time")
        if (not isinstance(candidate, (int, float)) or isinstance(candidate, bool) or
                not isfinite(candidate)):
            raise ValidationError("The point time must be finite.")
        channel = service.recording.channel(annotation.channel_id)
        if not 0 <= candidate <= channel.duration:
            raise ValidationError("The point time is outside the channel.")
        moved = service.move_point(annotation.id, candidate)
    except ValidationError as exc:
        return (visible_workspace, visible_catalog, state,
                f"Move selected point failed: {exc}",
                {"kind": f"{kind}-failed"})
    return (service.workspace, _service_catalog(service), state,
            "Selected amplitude point moved.",
            {"kind": kind, "annotation_id": moved.id,
             "time": moved.location.start.seconds})


def numeric_drafts_after_creation(result: dict[str, object], point_time: object,
                                  interval_start: object, interval_end: object) -> tuple[object, object, object]:
    """Clear only the numeric draft committed by a successful create command."""

    if result.get("kind") == "create-amplitude-marker":
        return None, interval_start, interval_end
    if result.get("kind") == "create-interval":
        return point_time, None, None
    return point_time, interval_start, interval_end


def help_dialog_open(triggered_id: str | None, is_open: object) -> bool:
    """Resolve the Help dialog state without coupling it to product actions."""

    if triggered_id == HELP_OPEN_ID:
        return True
    if triggered_id == HELP_CLOSE_ID:
        return False
    return bool(is_open)


def create_dash_app(recording: Recording | None = None,
                    workspace: Workspace | None = None, service: object | None = None) -> Dash:
    """Create a locally-served app; recording/workspace injection is testable state."""

    if service is not None:
        if recording is None:
            recording = service.recording
        elif recording != service.recording:
            raise ValueError("The injected recording does not match the annotation service.")
        if workspace is not None and workspace != service.workspace:
            raise ValueError("The injected workspace does not match the annotation service.")
        workspace = service.workspace
    elif recording is not None and workspace is not None and workspace.recording_id != recording.id:
        raise ValueError("The injected workspace does not match the recording.")
    initial_state = _initial_state(recording)
    initial_viewport = initial_state.get("viewport")
    if not isinstance(initial_viewport, dict):
        initial_viewport = {"start": 0.0, "width": 8.0}
    initial_catalog = _service_catalog(service)
    initial_notes = visible_notes(workspace) if workspace is not None else ""

    app = Dash(
        __name__,
        assets_folder=str(ASSETS_DIRECTORY),
        assets_url_path="assets",
        serve_locally=True,
        title="Signal annotation",
        prevent_initial_callbacks=True,
    )
    app.layout = html.Main(
        id="signal-annotation-app",
        className="signal-annotation-app",
        **{"data-has-selection": "false"},
        children=[
            dcc.Store(id=STATE_ID, data=initial_state, storage_type="memory"),
            dcc.Store(id=BRIDGE_ID, data=None, storage_type="memory"),
            dcc.Store(id=DISPATCH_ID, data={"kind": "idle"}, storage_type="memory"),
            html.Header([
                html.H1("Signal annotation"),
                html.Button("Help", id=HELP_OPEN_ID, type="button"),
            ], className="app-header"),
            html.Section([
                html.H2("Mode"),
                dcc.RadioItems(
                    id=MODE_ID,
                    options=[
                        {"label": "Navigate", "value": "navigate"},
                        {"label": "Mark amplitude", "value": "mark-amplitude"},
                        {"label": "Mark interval", "value": "mark-interval"},
                    ],
                    value=DEFAULT_MODE,
                    inline=True,
                ),
            ], className="mode-controls", **{"aria-label": "Annotation mode"}),
            html.Section([
                html.Label("Start (s)", htmlFor=START_INPUT_ID),
                dcc.Input(
                    id=START_INPUT_ID,
                    type="number",
                    value=initial_viewport["start"],
                    min=0,
                    step="any",
                ),
                html.Label("Width (s)", htmlFor=WIDTH_INPUT_ID),
                dcc.Input(
                    id=WIDTH_INPUT_ID,
                    type="number",
                    value=initial_viewport["width"],
                    min=0.001,
                    step="any",
                ),
                html.Button("Go", id=GO_BUTTON_ID, type="button"),
                html.Button("Full view", id=FULL_VIEW_BUTTON_ID, type="button"),
                html.Button("Reload saved state", id=RELOAD_BUTTON_ID, type="button"),
            ], className="time-controls", **{"aria-label": "Time navigation"}),
            html.Section([
                html.H2("Amplitude labels"),
                html.Div([
                    dcc.Input(id=AMPLITUDE_NAME_INPUT_ID, type="text", value="",
                              placeholder="New amplitude label"),
                    html.Button("Add amplitude label", id=AMPLITUDE_ADD_BUTTON_ID, type="button"),
                    dcc.Dropdown(
                        id=AMPLITUDE_SELECT_ID,
                        options=catalog_options(initial_catalog, "amplitude_labels"),
                        value=None,
                        placeholder="Choose an amplitude label",
                        clearable=True,
                    ),
                    html.Button(
                        "Apply label to selected point",
                        id=REASSIGN_POINT_BUTTON_ID,
                        type="button",
                    ),
                    html.Span(catalog_status(initial_catalog, "amplitude_labels"),
                              id=AMPLITUDE_STATUS_ID),
                ], className="catalog-control-row"),
                html.H2("Interval labels"),
                html.Div([
                    dcc.Input(id=INTERVAL_NAME_INPUT_ID, type="text", value="",
                              placeholder="New interval label"),
                    html.Button("Add interval label", id=INTERVAL_ADD_BUTTON_ID, type="button"),
                    dcc.Dropdown(
                        id=INTERVAL_SELECT_ID,
                        options=catalog_options(initial_catalog, "interval_labels"),
                        value=None,
                        placeholder="Choose an interval label",
                        clearable=True,
                    ),
                    html.Span(catalog_status(initial_catalog, "interval_labels"),
                              id=INTERVAL_STATUS_ID),
                ], className="catalog-control-row"),
            ], className="catalog-controls", **{"aria-label": "Shared label catalogs"}),
            html.Section([
                html.H2("Create annotations"),
                html.Div(active_channel_text(recording, initial_state), id=ACTIVE_CHANNEL_ID),
                html.Div([
                    html.Label("Point time (s)", htmlFor=POINT_TIME_INPUT_ID),
                    dcc.Input(id=POINT_TIME_INPUT_ID, type="number", value=None, step="any"),
                    html.Button("Create amplitude point", id=CREATE_POINT_BUTTON_ID, type="button"),
                ], className="numeric-creation-row"),
                html.Div([
                    html.Label("Interval start (s)", htmlFor=INTERVAL_START_INPUT_ID),
                    dcc.Input(id=INTERVAL_START_INPUT_ID, type="number", value=None, step="any"),
                    html.Label("Interval end (s)", htmlFor=INTERVAL_END_INPUT_ID),
                    dcc.Input(id=INTERVAL_END_INPUT_ID, type="number", value=None, step="any"),
                    html.Button("Create interval", id=CREATE_INTERVAL_BUTTON_ID, type="button"),
                ], className="numeric-creation-row"),
            ], className="numeric-creation-controls", **{"aria-label": "Numeric annotation creation"}),
            html.Section([
                html.H2("General notes"),
                dcc.Textarea(id=GENERAL_NOTES_ID, value=initial_notes,
                             placeholder="General recording notes"),
                html.Button("Save notes", id=SAVE_NOTES_BUTTON_ID, type="button"),
                html.Button("Clear notes", id=CLEAR_NOTES_BUTTON_ID, type="button"),
                html.Button("Export current CSV", id=EXPORT_CSV_BUTTON_ID, type="button"),
            ], className="notes-controls", **{"aria-label": "Notes and export"}),
            html.Section(
                dcc.Graph(
                    id=GRAPH_ID,
                    figure=signal_figure(recording, workspace, initial_state),
                    config={"responsive": True, "scrollZoom": True, "displaylogo": False},
                ),
                className="plot-region",
                **{"aria-label": "Signal plot"},
            ),
            html.Div("Ready.", id=STATUS_ID, role="status", **{"aria-live": "polite"}),
            html.Dialog([
                html.H2("Help"),
                html.P("Use pan, zoom, the range slider, or the time controls to navigate signals. In Mark amplitude, choose a label and click a signal sample to create a point. In Navigate, click an existing point to select it, then drag its dotted vertical time handle horizontally to move it to the nearest signal sample. Apply the chosen amplitude label with the explicit action, or press Delete or Backspace to remove the selected point."),
                html.Button("Close", id=HELP_CLOSE_ID, type="button"),
            ], id=HELP_DIALOG_ID, open=False),
        ],
    )

    visible_workspace = workspace
    visible_catalog = initial_catalog

    @app.callback(
        Output(STATE_ID, "data"),
        Output(STATUS_ID, "children"),
        Output(DISPATCH_ID, "data"),
        Output("signal-annotation-app", "data-has-selection"),
        Output(GRAPH_ID, "figure"),
        Output(START_INPUT_ID, "value"),
        Output(WIDTH_INPUT_ID, "value"),
        Output(AMPLITUDE_SELECT_ID, "options"),
        Output(AMPLITUDE_SELECT_ID, "value"),
        Output(AMPLITUDE_STATUS_ID, "children"),
        Output(INTERVAL_SELECT_ID, "options"),
        Output(INTERVAL_SELECT_ID, "value"),
        Output(INTERVAL_STATUS_ID, "children"),
        Output(GENERAL_NOTES_ID, "value"),
        Output(AMPLITUDE_NAME_INPUT_ID, "value"),
        Output(INTERVAL_NAME_INPUT_ID, "value"),
        Output(ACTIVE_CHANNEL_ID, "children"),
        Output(POINT_TIME_INPUT_ID, "value"),
        Output(INTERVAL_START_INPUT_ID, "value"),
        Output(INTERVAL_END_INPUT_ID, "value"),
        Input(MODE_ID, "value"),
        Input(BRIDGE_ID, "data"),
        Input(GRAPH_ID, "relayoutData"),
        Input(GRAPH_ID, "clickData"),
        Input(GO_BUTTON_ID, "n_clicks"),
        Input(FULL_VIEW_BUTTON_ID, "n_clicks"),
        Input(RELOAD_BUTTON_ID, "n_clicks"),
        Input(AMPLITUDE_ADD_BUTTON_ID, "n_clicks"),
        Input(INTERVAL_ADD_BUTTON_ID, "n_clicks"),
        Input(AMPLITUDE_SELECT_ID, "value"),
        Input(REASSIGN_POINT_BUTTON_ID, "n_clicks"),
        Input(INTERVAL_SELECT_ID, "value"),
        Input(SAVE_NOTES_BUTTON_ID, "n_clicks"),
        Input(CLEAR_NOTES_BUTTON_ID, "n_clicks"),
        Input(EXPORT_CSV_BUTTON_ID, "n_clicks"),
        Input(CREATE_POINT_BUTTON_ID, "n_clicks"),
        Input(CREATE_INTERVAL_BUTTON_ID, "n_clicks"),
        State(STATE_ID, "data"),
        State(START_INPUT_ID, "value"),
        State(WIDTH_INPUT_ID, "value"),
        State(AMPLITUDE_NAME_INPUT_ID, "value"),
        State(INTERVAL_NAME_INPUT_ID, "value"),
        State(GENERAL_NOTES_ID, "value"),
        State(POINT_TIME_INPUT_ID, "value"),
        State(INTERVAL_START_INPUT_ID, "value"),
        State(INTERVAL_END_INPUT_ID, "value"),
    )
    def canonical_dispatcher(mode: object, bridge_event: object, relayout_data: object, click_data: object,
                             go_clicks: object, full_clicks: object, reload_clicks: object,
                             add_amplitude_clicks: object, add_interval_clicks: object,
                             selected_amplitude: object, reassign_point_clicks: object,
                             selected_interval: object,
                             save_notes_clicks: object, clear_notes_clicks: object,
                             export_csv_clicks: object, create_point_clicks: object,
                             create_interval_clicks: object,
                             state: object,
                             start: object, width: object, amplitude_name: object,
                             interval_name: object, notes: object, point_time: object,
                             interval_start: object, interval_end: object):
        """The only callback owning transient state, navigation, and the figure."""

        nonlocal visible_workspace, visible_catalog
        triggered = callback_context.triggered_id
        if (triggered == BRIDGE_ID and isinstance(bridge_event, dict) and
                bridge_event.get("type") == "graph-click"):
            # ``dcc.Graph.clickData`` is the one native source of active-channel
            # context.  Do not let the retained observation bridge re-submit stale
            # State after that click.
            return (no_update,) * 20
        prop_id = callback_context.triggered[0]["prop_id"] if callback_context.triggered else ""
        native_marker_action = None
        if triggered == MODE_ID:
            value = mode
        elif triggered == BRIDGE_ID:
            value = bridge_event
        elif triggered == GRAPH_ID:
            if prop_id == f"{GRAPH_ID}.clickData":
                triggered, value = "signal-graph-click", click_data
            else:
                value = relayout_data
                native_marker_action = normalize_selected_point_relayout(
                    relayout_data,
                    selected_marker_shape_manifest(
                        recording, visible_workspace, state,
                    ),
                )
        elif triggered == GO_BUTTON_ID:
            value = {"start": start, "end": _end_from_start_and_width(start, width)}
        elif triggered == FULL_VIEW_BUTTON_ID:
            value = full_clicks
        elif triggered == RELOAD_BUTTON_ID:
            value = reload_clicks
        elif triggered == AMPLITUDE_ADD_BUTTON_ID:
            value = amplitude_name
        elif triggered == INTERVAL_ADD_BUTTON_ID:
            value = interval_name
        elif triggered == AMPLITUDE_SELECT_ID:
            value = selected_amplitude
        elif triggered == REASSIGN_POINT_BUTTON_ID:
            value = reassign_point_clicks
        elif triggered == INTERVAL_SELECT_ID:
            value = selected_interval
        elif triggered == SAVE_NOTES_BUTTON_ID:
            value = notes
        elif triggered in {CLEAR_NOTES_BUTTON_ID, EXPORT_CSV_BUTTON_ID}:
            value = None
        elif triggered == CREATE_POINT_BUTTON_ID:
            value = point_time
        elif triggered == CREATE_INTERVAL_BUTTON_ID:
            value = {"start": interval_start, "end": interval_end}
        else:
            value = None
        action = native_marker_action or normalize_action(triggered, value)
        prior_state = state
        if triggered == "signal-graph-click":
            action = validate_native_graph_action(
                action, recording, visible_workspace, state,
            )
        next_state, status, result = dispatch(
            action,
            state,
            duration=recording.duration if recording is not None else None,
            active_channel_ids=tuple(channel.id for channel in recording.channels[:8]) if recording is not None else (),
        )
        if action is not None and action["kind"] in {
                "add-amplitude-label", "add-interval-label", "save-general-notes",
                "clear-general-notes", "export-csv", "reload"}:
            (visible_workspace, visible_catalog, next_state, status, result,
             visible_note_text, next_amplitude_name, next_interval_name) = execute_durable_action(
                 action, service, visible_workspace, visible_catalog, next_state,
                 amplitude_name=amplitude_name, interval_name=interval_name, notes=notes)
            next_point_time, next_interval_start, next_interval_end = point_time, interval_start, interval_end
        elif action is not None and action["kind"] in {"create-amplitude-marker", "create-interval"}:
            (visible_workspace, visible_catalog, next_state, status,
             result) = execute_numeric_creation(
                 action, service, visible_workspace, visible_catalog, next_state)
            next_point_time, next_interval_start, next_interval_end = numeric_drafts_after_creation(
                result, point_time, interval_start, interval_end)
            visible_note_text = visible_notes(visible_workspace) if visible_workspace is not None else ""
            next_amplitude_name, next_interval_name = amplitude_name, interval_name
        elif action is not None and action["kind"] == "set-active-channel":
            (visible_workspace, visible_catalog, next_state, graph_status,
             graph_result) = execute_graphical_marker_click(
                 action, service, visible_workspace, visible_catalog,
                 next_state, prior_state,
            )
            if graph_status is not None:
                status = graph_status
            if graph_result is not None:
                result = graph_result
            visible_note_text = visible_notes(visible_workspace) if visible_workspace is not None else ""
            next_amplitude_name, next_interval_name = amplitude_name, interval_name
            next_point_time, next_interval_start, next_interval_end = point_time, interval_start, interval_end
        elif action is not None and action["kind"] in {
                "reassign-selected-point", "delete-request"}:
            (visible_workspace, visible_catalog, next_state, status,
             result) = execute_selected_marker_action(
                 action, service, visible_workspace, visible_catalog, next_state,
            )
            visible_note_text = visible_notes(visible_workspace) if visible_workspace is not None else ""
            next_amplitude_name, next_interval_name = amplitude_name, interval_name
            next_point_time, next_interval_start, next_interval_end = point_time, interval_start, interval_end
        elif action is not None and action["kind"] == "move-selected-point":
            (visible_workspace, visible_catalog, next_state, status,
             result) = execute_selected_marker_move(
                 action, service, visible_workspace, visible_catalog, next_state,
            )
            visible_note_text = visible_notes(visible_workspace) if visible_workspace is not None else ""
            next_amplitude_name, next_interval_name = amplitude_name, interval_name
            next_point_time, next_interval_start, next_interval_end = point_time, interval_start, interval_end
        else:
            visible_note_text = visible_notes(visible_workspace) if visible_workspace is not None else ""
            next_amplitude_name = amplitude_name
            next_interval_name = interval_name
            next_point_time, next_interval_start, next_interval_end = point_time, interval_start, interval_end
        has_selection = "true" if next_state.get("selection") else "false"
        current_viewport = next_state.get("viewport")
        if not isinstance(current_viewport, dict):
            current_viewport = initial_viewport
        return (
            next_state,
            status,
            result,
            has_selection,
            signal_figure(recording, visible_workspace, next_state),
            current_viewport["start"],
            current_viewport["width"],
            catalog_options(visible_catalog, "amplitude_labels"),
            next_state.get("selected_amplitude_catalog_id"),
            catalog_status(visible_catalog, "amplitude_labels"),
            catalog_options(visible_catalog, "interval_labels"),
            next_state.get("selected_interval_catalog_id"),
            catalog_status(visible_catalog, "interval_labels"),
            visible_note_text,
            next_amplitude_name,
            next_interval_name,
            active_channel_text(recording, next_state),
            next_point_time,
            next_interval_start,
            next_interval_end,
        )

    @app.callback(
        Output(HELP_DIALOG_ID, "open"),
        Input(HELP_OPEN_ID, "n_clicks"),
        Input(HELP_CLOSE_ID, "n_clicks"),
        State(HELP_DIALOG_ID, "open"),
    )
    def toggle_help(open_clicks: object, close_clicks: object, is_open: object) -> bool:
        """Open/close Help without touching product or transient annotation state."""

        return help_dialog_open(callback_context.triggered_id, is_open)

    return app


def _end_from_start_and_width(start: object, width: object) -> object:
    """Keep numeric parsing in the pure dispatcher path rather than the layout."""

    try:
        return float(start) + float(width)
    except (TypeError, ValueError, OverflowError):
        return None
