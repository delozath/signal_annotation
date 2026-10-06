"""Framework-free presentation data for the future Plotly adapter.

The presenter reads recording/workspace state and returns only deterministic,
JSON-compatible dictionaries.  It deliberately contains no Plotly or Dash objects.
"""

from math import isfinite
from urllib.parse import quote

from ..application import interval_color, point_colors
from ..domain import Recording, Workspace
from .display import visible_samples


DEFAULT_VIEWPORT_SECONDS = 8.0
DEFAULT_SAMPLE_BUDGET = 4000
MAX_RENDERED_CHANNELS = 8
_TRACE_COLORS = (
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
)


def bounded_viewport(recording: Recording, start=None, end=None) -> dict:
    """Return a finite requested window constrained to recording bounds.

    Missing or non-finite input selects the same initial eight-second window as the
    existing application. Reversed or zero-width input becomes the smallest positive
    window used by the current navigation behavior.
    """
    duration = float(recording.duration)
    default_end = min(DEFAULT_VIEWPORT_SECONDS, duration)
    try:
        requested_start = 0.0 if start is None else float(start)
        requested_end = default_end if end is None else float(end)
    except (TypeError, ValueError, OverflowError):
        requested_start, requested_end = 0.0, default_end
    if not isfinite(requested_start) or not isfinite(requested_end):
        requested_start, requested_end = 0.0, default_end

    width = requested_end - requested_start
    if width >= duration:
        bounded_start, bounded_end = 0.0, duration
    else:
        if width <= 0:
            width = min(0.001, duration)
        bounded_start = min(max(requested_start, 0.0), duration - width)
        bounded_end = bounded_start + width
    return {
        "start": float(bounded_start),
        "end": float(bounded_end),
        "width": float(bounded_end - bounded_start),
        "duration": duration,
    }


def _identity(kind: str, *parts: str) -> str:
    """Build a delimiter-safe identity without using mutable projection positions."""
    return "/".join((kind, *(quote(str(part), safe="") for part in parts)))


def _json_values(values) -> list:
    """Convert display samples to strict JSON values, retaining gaps as nulls."""
    result = []
    for value in values:
        number = float(value)
        result.append(number if isfinite(number) else None)
    return result


def _channel_projection(recording: Recording, channel, viewport: dict, budget: int) -> dict:
    times, values = visible_samples(channel, viewport["start"], viewport["end"], budget)
    projection_id = _identity("channel", channel.id)
    return {
        "projection_id": projection_id,
        "recording_id": recording.id,
        "channel_id": channel.id,
        "name": channel.name,
        "title": f"{channel.name} · {channel.sample_rate:g} Hz",
        "units": channel.units,
        "sample_rate_hz": float(channel.sample_rate),
        "x": [float(value) for value in times],
        "y": _json_values(values),
    }


def _point_projection(recording: Recording, channel, annotation, color: str,
                      *, has_relationships: bool) -> dict:
    projection_id = _identity("point", annotation.id, "channel", channel.id)
    symbol = {
        "maximum": "triangle-up",
        "minimum": "triangle-down",
    }.get(annotation.kind, "circle")
    seconds, value = float(annotation.start), float(annotation.value)
    return {
        "projection_id": projection_id,
        "recording_id": recording.id,
        "annotation_id": annotation.id,
        "channel_id": channel.id,
        "x": seconds,
        "y": value,
        "label": annotation.label.text,
        "color": color,
        "symbol": symbol,
        "origin": annotation.origin,
        "annotation_kind": annotation.kind,
        "movable": (
            annotation.origin == "manual" and
            annotation.kind == "marker" and
            not has_relationships
        ),
        "hover": {
            "label": annotation.label.text,
            "time_seconds": seconds,
            "value": value,
            "origin": annotation.origin,
        },
    }


def _interval_projection(recording: Recording, workspace: Workspace, channel,
                         annotation) -> dict:
    projection_id = _identity("interval", annotation.id, "channel", channel.id)
    start, end = float(annotation.start), float(annotation.end)
    return {
        "projection_id": projection_id,
        "recording_id": recording.id,
        "annotation_id": annotation.id,
        "channel_id": channel.id,
        "x0": start,
        "x1": end,
        "label": annotation.label.text,
        "color": interval_color(annotation, workspace),
        "origin": annotation.origin,
        "hover": {
            "label": annotation.label.text,
            "start_seconds": start,
            "end_seconds": end,
            "origin": annotation.origin,
        },
    }


def build_presentation(recording: Recording, workspace: Workspace, *, start=None,
                       end=None, sample_budget: int = DEFAULT_SAMPLE_BUDGET) -> dict:
    """Project current state into deterministic data for a future Plotly figure.

    Point and interval identities are repeated in a manifest so later callbacks can
    resolve browser projections without inspecting label, color, or mutable positions.
    Region and recording annotations remain in ``workspace`` but are intentionally not
    projected because the current graph does not edit them.
    """
    if type(sample_budget) is not int or sample_budget <= 0:
        raise ValueError("sample_budget must be a positive integer.")

    viewport = bounded_viewport(recording, start, end)
    # The native Plotly view is intentionally bounded to one readable stack.  Keep the
    # recording's source order: channel/trace identity must never depend on a mutable
    # UI ordering or a label.
    visible_channels = recording.channels[:MAX_RENDERED_CHANNELS]
    channels = [
        _channel_projection(recording, channel, viewport, sample_budget)
        for channel in visible_channels
    ]
    colors = point_colors(workspace.annotations)
    related_annotation_ids = {
        endpoint.id
        for relationship in workspace.relationships
        for endpoint in (relationship.source, relationship.target)
        if endpoint.type == "annotation"
    }
    points = []
    intervals = []
    for channel in visible_channels:
        for annotation in workspace.annotations:
            if (annotation.location.type == "point" and
                    annotation.channel_id == channel.id):
                points.append(_point_projection(
                    recording, channel, annotation, colors[annotation.id],
                    has_relationships=annotation.id in related_annotation_ids))
            elif (annotation.location.type == "interval" and
                  (not annotation.location.channel_ids or
                   channel.id in annotation.location.channel_ids) and
                  annotation.end >= viewport["start"] and
                  annotation.start <= viewport["end"]):
                intervals.append(_interval_projection(
                    recording, workspace, channel, annotation))

    channel_manifest = {
        item["projection_id"]: {
            "recording_id": item["recording_id"],
            "channel_id": item["channel_id"],
        }
        for item in channels
    }
    point_manifest = {
        item["projection_id"]: {
            "recording_id": item["recording_id"],
            "channel_id": item["channel_id"],
            "annotation_id": item["annotation_id"],
        }
        for item in points
    }
    interval_manifest = {
        item["projection_id"]: {
            "recording_id": item["recording_id"],
            "channel_id": item["channel_id"],
            "annotation_id": item["annotation_id"],
        }
        for item in intervals
    }
    return {
        "recording_id": recording.id,
        "viewport": viewport,
        "channels": channels,
        "points": points,
        "intervals": intervals,
        "manifest": {
            "channels": channel_manifest,
            "points": point_manifest,
            "intervals": interval_manifest,
        },
    }


def _interval_bounds(channel: dict) -> tuple[float, float]:
    """Return a finite channel-height for a read-only interval target."""
    values = [value for value in channel.get("y", ()) if value is not None]
    if not values:
        return -1.0, 1.0
    low, high = min(values), max(values)
    if low == high:
        padding = max(abs(low) * 0.05, 1.0)
        return low - padding, high + padding
    return low, high


def _identity_customdata(projection: dict, kind: str, count: int) -> list[list[str]]:
    """Repeat server-owned identity for every Plotly datum in a target trace."""
    entry = [
        projection["projection_id"],
        projection["annotation_id"],
        projection["channel_id"],
        kind,
    ]
    return [list(entry) for _ in range(count)]


def _signal_customdata(channel: dict) -> list[list[str]]:
    """Repeat bounded native click identity for the rendered signal samples."""

    entry = [channel["projection_id"], channel["channel_id"], "signal"]
    return [list(entry) for _ in channel["x"]]


def _selected_annotation(state: object) -> str | None:
    """Read the reserved visual-only selection path without choosing a selection."""
    if not isinstance(state, dict):
        return None
    selected = state.get("selected_visual")
    if not isinstance(selected, dict):
        return None
    annotation_id = selected.get("annotation_id")
    return annotation_id if isinstance(annotation_id, str) else None


def _selected_point_handle(state: object, points: list[dict]) -> dict | None:
    """Resolve one eligible selected point without trusting display geometry."""

    if not isinstance(state, dict) or state.get("mode") != "navigate":
        return None
    selection = state.get("selection")
    if (not isinstance(selection, dict) or
            set(selection) != {"projection_id", "annotation_id", "channel_id", "kind"} or
            selection.get("kind") != "point" or
            state.get("selected_visual") != {
                "annotation_id": selection.get("annotation_id"), "kind": "point",
            }):
        return None
    matches = [
        point for point in points
        if point.get("projection_id") == selection.get("projection_id") and
        point.get("annotation_id") == selection.get("annotation_id") and
        point.get("channel_id") == selection.get("channel_id") and
        point.get("movable") is True
    ]
    return matches[0] if len(matches) == 1 else None


def build_signal_figure(presentation: dict, selected_visual: object = None) -> dict:
    """Build a schema-compatible stacked Plotly figure with read-only annotations.

    It remains free of Plotly imports.  Point and interval traces carry every stable
    server identity in both ``meta`` and ``customdata`` so later selection must never
    infer an annotation from color, text, trace order, or geometry.
    """

    channels = list(presentation.get("channels", ()))[:MAX_RENDERED_CHANNELS]
    viewport = presentation.get("viewport", {})
    start = float(viewport.get("start", 0.0))
    end = float(viewport.get("end", DEFAULT_VIEWPORT_SECONDS))
    count = len(channels)
    if not count:
        return {"data": [], "layout": {"template": "plotly_white", "uirevision": "signals"}}

    data = []
    layout: dict[str, object] = {
        "template": "plotly_white",
        "showlegend": False,
        "hovermode": "x unified",
        "dragmode": "pan",
        "uirevision": f"signals:{presentation.get('recording_id', '')}",
        "height": max(360, 230 * count),
        "margin": {"l": 78, "r": 24, "t": 28, "b": 54},
    }
    gap = 0.025
    available_height = 1.0 - gap * (count - 1)
    row_height = available_height / count
    selected_annotation = _selected_annotation(selected_visual)
    selected_point_handle = _selected_point_handle(
        selected_visual, list(presentation.get("points", ())),
    )
    points_by_channel = {}
    intervals_by_channel = {}
    for point in presentation.get("points", ()):
        points_by_channel.setdefault(point["channel_id"], []).append(point)
    for interval in presentation.get("intervals", ()):
        intervals_by_channel.setdefault(interval["channel_id"], []).append(interval)

    for index, channel in enumerate(channels, start=1):
        axis_suffix = "" if index == 1 else str(index)
        xaxis_name = "xaxis" + axis_suffix
        yaxis_name = "yaxis" + axis_suffix
        trace_xaxis = "x" + ("" if index == 1 else str(index))
        trace_yaxis = "y" + ("" if index == 1 else str(index))
        # Plotly domains are specified bottom-to-top while the recording order is
        # displayed top-to-bottom.
        top = 1.0 - (index - 1) * (row_height + gap)
        domain = [max(0.0, top - row_height), min(1.0, top)]
        layout[yaxis_name] = {
            "domain": domain,
            "title": {"text": f"{channel['name']} ({channel['units']})"},
            "zeroline": False,
        }
        xaxis = {
            "anchor": trace_yaxis,
            "range": [start, end],
            "showticklabels": index == count,
            "zeroline": False,
        }
        if index == count:
            xaxis["title"] = {"text": "Time (s)"}
        if index != 1:
            xaxis["matches"] = "x"
        if index == count:
            xaxis["rangeslider"] = {"visible": True}
        layout[xaxis_name] = xaxis
        data.append({
            "type": "scattergl",
            "mode": "lines",
            "name": channel["name"],
            "uid": channel["projection_id"],
            "meta": {
                "projection_id": channel["projection_id"],
                "recording_id": channel["recording_id"],
                "channel_id": channel["channel_id"],
            },
            "xaxis": trace_xaxis,
            "yaxis": trace_yaxis,
            "x": channel["x"],
            "y": channel["y"],
            # ``visible_samples`` bounds this list by the fixed per-channel budget.
            # It is the sole source used to map native clickData to channel context.
            "customdata": _signal_customdata(channel),
            "line": {"color": _TRACE_COLORS[index - 1], "width": 1},
            "hovertemplate": "%{y}<extra>" + channel["name"] + "</extra>",
        })
        for point in points_by_channel.get(channel["channel_id"], ()):
            is_selected = point["annotation_id"] == selected_annotation
            data.append({
                "type": "scatter",
                "mode": "markers",
                "name": point["label"],
                "uid": point["projection_id"],
                "meta": {
                    "projection_id": point["projection_id"],
                    "recording_id": point["recording_id"],
                    "annotation_id": point["annotation_id"],
                    "channel_id": point["channel_id"],
                    "kind": "point",
                },
                "xaxis": trace_xaxis,
                "yaxis": trace_yaxis,
                "x": [point["x"]],
                "y": [point["y"]],
                "customdata": _identity_customdata(point, "point", 1),
                "marker": {
                    "color": point["color"],
                    "symbol": point["symbol"],
                    "size": 12 if is_selected else 10,
                    "line": {"color": "#111827", "width": 2 if is_selected else 0},
                },
                "hovertemplate": (
                    f"Label: {point['hover']['label']}<br>"
                    "Time: %{x:.6g} s<br>Value: %{y:.6g}<br>"
                    f"Origin: {point['hover']['origin']}<extra></extra>"
                ),
            })
        low, high = _interval_bounds(channel)
        for interval in intervals_by_channel.get(channel["channel_id"], ()):
            is_selected = interval["annotation_id"] == selected_annotation
            data.append({
                # A filled scatter polygon is both the read-only interval band and
                # the identity-bearing click target for the later selection stage.
                "type": "scatter",
                "mode": "lines",
                "name": interval["label"],
                "uid": interval["projection_id"],
                "meta": {
                    "projection_id": interval["projection_id"],
                    "recording_id": interval["recording_id"],
                    "annotation_id": interval["annotation_id"],
                    "channel_id": interval["channel_id"],
                    "kind": "interval",
                },
                "xaxis": trace_xaxis,
                "yaxis": trace_yaxis,
                "x": [interval["x0"], interval["x1"], interval["x1"], interval["x0"], interval["x0"]],
                "y": [low, low, high, high, low],
                "customdata": _identity_customdata(interval, "interval", 5),
                "fill": "toself",
                "fillcolor": interval["color"],
                "opacity": 0.34 if is_selected else 0.22,
                "line": {"color": interval["color"], "width": 2 if is_selected else 1},
                "hoveron": "fills",
                "hovertemplate": (
                    f"Label: {interval['hover']['label']}<br>"
                    f"Start: {interval['hover']['start_seconds']:.6g} s<br>"
                    f"End: {interval['hover']['end_seconds']:.6g} s<br>"
                    f"Origin: {interval['hover']['origin']}<extra></extra>"
                ),
            })
        if (selected_point_handle is not None and
                selected_point_handle["channel_id"] == channel["channel_id"]):
            shape_index = len(layout.setdefault("shapes", []))
            layout["shapes"].append({
                "type": "line",
                "x0": selected_point_handle["x"],
                "x1": selected_point_handle["x"],
                "y0": 0,
                "y1": 1,
                "xref": trace_xaxis,
                "yref": f"{trace_yaxis} domain",
                "editable": True,
                "layer": "above",
                "name": (
                    "selected-point-time-handle:"
                    f"{selected_point_handle['projection_id']}"
                ),
                "line": {"color": "#d62728", "width": 4, "dash": "dot"},
            })
            layout.setdefault("meta", {}).setdefault("shape_by_index", {})[
                str(shape_index)
            ] = {
                "kind": "point-time-handle",
                "role": "point",
                "projection_id": selected_point_handle["projection_id"],
                "annotation_id": selected_point_handle["annotation_id"],
                "channel_id": selected_point_handle["channel_id"],
            }
    return {"data": data, "layout": layout}
