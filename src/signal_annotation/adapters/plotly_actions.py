"""Pure transient action handling for the Dash adapter.

This module deliberately has no repository or application-service dependency.  The
shell can therefore establish one callback owner before later stages add durable
commands to it.
"""

from __future__ import annotations

from math import isfinite
import re

DEFAULT_MODE = "navigate"
MODES = frozenset({"navigate", "mark-amplitude", "mark-interval"})
_SHAPE_FIELD = re.compile(r"^shapes\[(\d+)\]\.(x0|x1|y0|y1)$")
_WHOLE_SHAPE = re.compile(r"^shapes\[(\d+)\]$")


def initial_transient_state() -> dict[str, object]:
    """Return browser-only state; none of these values are durable product data."""

    return {
        "mode": DEFAULT_MODE,
        "selection": None,
        # Reserved for the later one-annotation selection/highlight stages.  Keeping
        # this browser-only container now makes the visual path explicit without
        # interpreting clicks or changing a saved annotation.
        "selected_visual": {"annotation_id": None, "kind": None},
        "viewport": None,
        "pending_click": None,
        "active_channel": None,
        # These IDs are browser-only choices for the later creation stages.  The
        # durable catalog remains the service's source of truth.
        "selected_amplitude_catalog_id": None,
        "selected_interval_catalog_id": None,
        "event_nonce": 0,
    }


def _native_click_details(value: object) -> dict[str, object] | None:
    """Extract one bounded, server-owned target from native Plotly ``clickData``.

    Trace numbers, display names, colors, and screen geometry are deliberately not
    meaningful here.  Signal traces use ``[projection_id, channel_id, "signal"]``;
    read-only annotation targets retain their established four-field identity.
    """

    if not isinstance(value, dict):
        return None
    points = value.get("points")
    if not isinstance(points, list) or len(points) != 1 or not isinstance(points[0], dict):
        return None
    point = points[0]
    customdata = point.get("customdata")
    if not isinstance(customdata, (list, tuple)):
        return None
    if (len(customdata) == 3 and customdata[2] == "signal" and
            all(isinstance(item, str) for item in customdata[:2])):
        try:
            seconds = float(point.get("x"))
        except (TypeError, ValueError, OverflowError):
            return None
        if isinstance(point.get("x"), bool) or not isfinite(seconds):
            return None
        return {
            "graph_target": "signal",
            "projection_id": customdata[0],
            "channel_id": customdata[1],
            "time": seconds,
        }
    if (len(customdata) == 4 and customdata[3] in {"point", "interval"} and
            all(isinstance(item, str) for item in customdata[:3])):
        return {
            "graph_target": customdata[3],
            "projection_id": customdata[0],
            "annotation_id": customdata[1],
            "channel_id": customdata[2],
        }
    return None


def normalize_relayout_viewport(value: object) -> dict[str, object] | None:
    """Extract one x viewport from practical native Plotly relayout variants.

    Plotly has emitted both indexed and whole-array range values across interaction
    paths.  Matched subplots can report any x-axis key, so all x-axis ranges normalize
    to the same shared viewport.  Y-only relayout payloads are intentionally ignored.
    """

    if not isinstance(value, dict):
        return None
    ranges: dict[str, dict[int, object]] = {}
    for key, candidate in value.items():
        if not isinstance(key, str):
            continue
        if key.endswith(".autorange") and key.startswith("xaxis") and candidate is True:
            return {"kind": "full-view"}
        if not key.startswith("xaxis"):
            continue
        if key.endswith(".range") and isinstance(candidate, (list, tuple)) and len(candidate) == 2:
            return {"kind": "set-viewport", "start": candidate[0], "end": candidate[1]}
        prefix, separator, suffix = key.partition(".range[")
        if separator and suffix in {"0]", "1]"}:
            ranges.setdefault(prefix, {})[int(suffix[0])] = candidate
    for indexed in ranges.values():
        if 0 in indexed and 1 in indexed:
            return {"kind": "set-viewport", "start": indexed[0], "end": indexed[1]}
    return None


def normalize_selected_point_relayout(
        value: object, shape_by_index: object) -> dict[str, object] | None:
    """Normalize x-only edits for one server-identified selected-point handle.

    Plotly may emit a direct shape field, one whole shape, or the whole shapes list.
    Shape indices locate server-owned identity but never become annotation identity.
    Y changes are deliberately ignored so canonical Python redraw owns line height.
    """

    if not isinstance(value, dict) or not isinstance(shape_by_index, dict):
        return None
    fields: dict[tuple[str, str], object] = {}
    for key, candidate in value.items():
        if not isinstance(key, str):
            continue
        direct = _SHAPE_FIELD.fullmatch(key)
        if direct:
            fields[(direct.group(1), direct.group(2))] = candidate
            continue
        whole = _WHOLE_SHAPE.fullmatch(key)
        if whole and isinstance(candidate, dict):
            for field in ("x0", "x1", "y0", "y1"):
                if field in candidate:
                    fields[(whole.group(1), field)] = candidate[field]
    all_shapes = value.get("shapes")
    if isinstance(all_shapes, list):
        for index, shape in enumerate(all_shapes):
            if not isinstance(shape, dict):
                continue
            for field in ("x0", "x1", "y0", "y1"):
                if field in shape:
                    fields[(str(index), field)] = shape[field]

    candidates = []
    for index_text, identity in shape_by_index.items():
        if (not isinstance(index_text, str) or not isinstance(identity, dict) or
                identity.get("kind") != "point-time-handle" or
                identity.get("role") != "point"):
            continue
        x_fields = {
            field: fields[(index_text, field)]
            for field in ("x0", "x1")
            if (index_text, field) in fields
        }
        if not x_fields:
            continue
        numbers = []
        for candidate in x_fields.values():
            if isinstance(candidate, bool):
                numbers = []
                break
            try:
                number = float(candidate)
            except (TypeError, ValueError, OverflowError):
                numbers = []
                break
            if not isfinite(number):
                numbers = []
                break
            numbers.append(number)
        candidates.append({
            "kind": "move-selected-point",
            "role": "point",
            "projection_id": identity.get("projection_id"),
            "annotation_id": identity.get("annotation_id"),
            "channel_id": identity.get("channel_id"),
            "candidate_time": sum(numbers) / len(numbers) if numbers else None,
            "x_fields": x_fields,
        })
    return candidates[0] if len(candidates) == 1 else None


def _bounded_viewport(duration: object, start: object, end: object) -> dict[str, float] | None:
    """Keep browser-only navigation state finite and within a recording duration."""

    try:
        duration_number = float(duration)
        requested_start = float(start)
        requested_end = float(end)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(isfinite(item) for item in (duration_number, requested_start, requested_end)):
        return None
    if duration_number <= 0:
        return None
    width = requested_end - requested_start
    if width >= duration_number:
        bounded_start, bounded_end = 0.0, duration_number
    else:
        if width <= 0:
            width = min(0.001, duration_number)
        bounded_start = min(max(requested_start, 0.0), duration_number - width)
        bounded_end = bounded_start + width
    return {
        "start": float(bounded_start),
        "end": float(bounded_end),
        "width": float(bounded_end - bounded_start),
        "duration": duration_number,
    }


def normalize_action(triggered_id: str | None, value: object) -> dict[str, object] | None:
    """Turn shell inputs into a deliberately narrow, side-effect-free action."""

    if triggered_id == "annotation-mode-control" and value in MODES:
        return {"kind": "set-mode", "mode": value}
    if triggered_id == "bridge-event-store" and isinstance(value, dict):
        event_type = value.get("type")
        if event_type == "delete-request":
            return {"kind": event_type, "event": value}
        if event_type == "graph-click":
            # Native dcc.Graph clickData is the canonical channel-context input.
            # The legacy observation bridge can arrive later with stale callback
            # State, so it must never own or replay a graph click.
            return {"kind": "ignored-bridge-graph-click"}
    if triggered_id == "signal-graph":
        return normalize_relayout_viewport(value)
    if triggered_id == "signal-graph-click":
        details = _native_click_details(value)
        return {"kind": "set-active-channel", **details} if details is not None else None
    if triggered_id == "go-time-window-button" and isinstance(value, dict):
        return {"kind": "set-viewport", "start": value.get("start"), "end": value.get("end")}
    if triggered_id == "full-view-button":
        return {"kind": "full-view"}
    if triggered_id == "reload-saved-state-button":
        return {"kind": "reload"}
    if triggered_id == "add-amplitude-label-button":
        return {"kind": "add-amplitude-label", "name": value}
    if triggered_id == "add-interval-label-button":
        return {"kind": "add-interval-label", "name": value}
    if triggered_id == "amplitude-label-select":
        return {"kind": "select-amplitude-label", "catalog_id": value}
    if triggered_id == "reassign-selected-point-button":
        return {"kind": "reassign-selected-point"}
    if triggered_id == "interval-label-select":
        return {"kind": "select-interval-label", "catalog_id": value}
    if triggered_id == "save-general-notes-button":
        return {"kind": "save-general-notes", "notes": value}
    if triggered_id == "clear-general-notes-button":
        return {"kind": "clear-general-notes"}
    if triggered_id == "export-csv-button":
        return {"kind": "export-csv"}
    if triggered_id == "create-amplitude-marker-button":
        return {"kind": "create-amplitude-marker", "time": value}
    if triggered_id == "create-interval-button" and isinstance(value, dict):
        return {"kind": "create-interval", "start": value.get("start"), "end": value.get("end")}
    return None


def dispatch(action: dict[str, object] | None, state: object, *, duration: object = None,
             active_channel_ids: object = None) -> tuple[dict[str, object], str, dict[str, object]]:
    """Apply one transient action and describe it without mutating product state.

    This is the single future dispatcher seam for all Dash actions.  Stage 33 only
    changes mode and reports bridge observations; it never calls a service or writes
    a sidecar/catalog.
    """

    next_state = dict(state) if isinstance(state, dict) else initial_transient_state()
    for key, value in initial_transient_state().items():
        next_state.setdefault(key, value)
    if action is None:
        return next_state, "Ready.", {"kind": "idle"}

    kind = action["kind"]
    if kind == "set-mode":
        next_state["mode"] = action["mode"]
        return next_state, f"Mode: {action['mode']}.", {"kind": kind}

    if kind == "set-active-channel":
        channel_id = action.get("channel_id")
        allowed = set(active_channel_ids) if isinstance(active_channel_ids, (list, tuple, set, frozenset)) else None
        if not isinstance(channel_id, str) or (allowed is not None and channel_id not in allowed):
            return next_state, "Ignored an unknown plot channel.", {"kind": "ignored-active-channel"}
        next_state["active_channel"] = channel_id
        return next_state, "Active channel updated.", {"kind": kind, "channel_id": channel_id}

    if kind == "ignored-bridge-graph-click":
        return next_state, "Native graph click handled.", {"kind": kind}

    if kind == "full-view":
        viewport = _bounded_viewport(duration, 0.0, duration)
        if viewport is None:
            return next_state, "No recording is loaded.", {"kind": "ignored-navigation"}
        next_state["viewport"] = viewport
        return next_state, "Full view.", {"kind": kind, "viewport": viewport}

    if kind == "set-viewport":
        viewport = _bounded_viewport(duration, action.get("start"), action.get("end"))
        if viewport is None:
            return next_state, "Enter a finite time window.", {"kind": "invalid-navigation"}
        next_state["viewport"] = viewport
        return next_state, "Time view updated.", {"kind": kind, "viewport": viewport}

    if kind == "reload":
        # The Dash composition root performs the validated service call.  This pure
        # state reducer deliberately does not import a repository or service.
        return next_state, "Reload requested.", {"kind": kind}

    if kind == "select-amplitude-label":
        next_state["selected_amplitude_catalog_id"] = action.get("catalog_id")
        return next_state, "Amplitude label selected.", {"kind": kind}

    if kind == "select-interval-label":
        next_state["selected_interval_catalog_id"] = action.get("catalog_id")
        return next_state, "Interval label selected.", {"kind": kind}

    # Durable actions are executed by the Dash composition root.  Keeping their
    # normalization here means the canonical dispatcher is still their sole UI owner.
    if kind in {"add-amplitude-label", "add-interval-label", "save-general-notes",
                "clear-general-notes", "export-csv", "create-amplitude-marker",
                "create-interval", "reassign-selected-point", "delete-request",
                "move-selected-point"}:
        return next_state, "Action requested.", {"kind": kind}

    # The JavaScript bridge is intentionally observational at this stage.  Later
    # stages validate any requested delete/click against the server-owned workspace.
    next_state["event_nonce"] = int(next_state.get("event_nonce", 0)) + 1
    return next_state, "Browser event received.", {"kind": kind}
