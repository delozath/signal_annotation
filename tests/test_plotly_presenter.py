import json
from copy import deepcopy

import numpy as np
import pytest

from signal_annotation.adapters.plotly_presenter import (
    MAX_RENDERED_CHANNELS, bounded_viewport, build_presentation, build_signal_figure,
)
from signal_annotation.adapters.readers import demo_recording
from signal_annotation.application import LEGACY_INTERVAL_COLOR, MARKER_COLORS
from signal_annotation.domain import (
    Amplitude, Annotation, Channel, Coordinate, Label, Location, Provenance, Recording,
    Workspace,
)
from signal_annotation.validation import (
    INTERVAL_LABEL_CATALOG_KEY, INTERVAL_LABEL_COLOR_ATTRIBUTE,
    INTERVAL_LABEL_COLORS, INTERVAL_LABEL_NAME_ATTRIBUTE,
)


def annotation(identity, location, code, text, attributes=None):
    return Annotation(identity, location, Label(code, text, attributes or {}),
                      Provenance("manual", "tester", "2026-01-01T00:00:00+00:00"))


def small_recording():
    return Recording("recording/id", "signal.acq", (
        Channel("fast/channel", "Fast", "mV", 4.0, (0.0, 1.0, np.nan, 3.0, 4.0)),
        Channel("slow", "Slow", "%", 2.0, (90.0, 91.0, 92.0)),
    ))


def test_viewport_defaults_and_normalizes_requested_bounds():
    recording = demo_recording()

    assert bounded_viewport(recording) == {
        "start": 0.0, "end": 8.0, "width": 8.0, "duration": recording.duration,
    }
    assert bounded_viewport(recording, -3, 5) == {
        "start": 0.0, "end": 8.0, "width": 8.0, "duration": recording.duration,
    }
    assert bounded_viewport(recording, 29, 37) == {
        "start": recording.duration - 8.0, "end": recording.duration,
        "width": 8.0, "duration": recording.duration,
    }
    reversed_window = bounded_viewport(recording, 5, 4)
    assert reversed_window["start"] == 5.0
    assert reversed_window["end"] == 5.001
    assert reversed_window["width"] == pytest.approx(0.001)
    assert reversed_window["duration"] == recording.duration
    assert bounded_viewport(recording, float("nan"), 2) == bounded_viewport(recording)


def test_demo_channels_are_stable_display_only_and_strict_json():
    recording = demo_recording()
    before = tuple(channel.values for channel in recording.channels)

    first = build_presentation(recording, Workspace(recording.id))
    second = build_presentation(recording, Workspace(recording.id))

    assert first == second
    assert [item["channel_id"] for item in first["channels"]] == [
        "ch0", "ch1", "ch2", "ch3", "ch4", "ch5",
    ]
    assert first["channels"][0]["title"] == "Synthetic ECG I · 500 Hz"
    assert first["channels"][0]["units"] == "mV"
    assert first["channels"][0]["sample_rate_hz"] == 500.0
    assert len(first["channels"][0]["x"]) <= 4000
    assert tuple(channel.values for channel in recording.channels) == before
    json.dumps(first, allow_nan=False)


def test_points_use_legacy_symbols_colors_hover_and_stable_manifest():
    recording = small_recording()
    workspace = Workspace(recording.id, annotations=[
        annotation("max/1", Location("point", ("fast/channel",), Coordinate(.25, 1),
                                     amplitude=Amplitude("mV", 1.0)),
                   "maximum", "Maximum"),
        annotation("min:2", Location("point", ("fast/channel",), Coordinate(.75, 3),
                                     amplitude=Amplitude("mV", 3.0)),
                   "minimum", "Minimum"),
        annotation("event", Location("point", ("slow",), Coordinate(.5, 1),
                                     amplitude=Amplitude("%", 91.0)),
                   "marker", "Event", {"color": MARKER_COLORS[7]}),
    ])

    result = build_presentation(recording, workspace)

    assert [item["symbol"] for item in result["points"]] == [
        "triangle-up", "triangle-down", "circle",
    ]
    assert [item["color"] for item in result["points"]] == [
        MARKER_COLORS[0], MARKER_COLORS[1], MARKER_COLORS[7],
    ]
    assert result["points"][0]["hover"] == {
        "label": "Maximum", "time_seconds": .25, "value": 1.0, "origin": "manual",
    }
    assert set(result["manifest"]["points"]) == {
        item["projection_id"] for item in result["points"]
    }
    assert len(set(result["manifest"]["points"])) == 3
    assert all(entry["annotation_id"] in {"max/1", "min:2", "event"}
               for entry in result["manifest"]["points"].values())


def test_interval_scope_projection_identity_and_unsupported_annotations_are_read_only():
    recording = small_recording()
    catalog_color = INTERVAL_LABEL_COLORS[0]
    catalog_attributes = {
        INTERVAL_LABEL_NAME_ATTRIBUTE: "Artifact",
        INTERVAL_LABEL_COLOR_ATTRIBUTE: catalog_color,
    }
    workspace = Workspace(recording.id, annotations=[
        annotation("local", Location("interval", ("fast/channel",), Coordinate(.1),
                                     Coordinate(.5)), "interval", "Local"),
        annotation("subset", Location("interval", ("slow",), Coordinate(.6),
                                      Coordinate(.8)), "interval", "Artifact",
                   catalog_attributes),
        annotation("global", Location("interval", (), Coordinate(.2), Coordinate(.9)),
                   "interval", "Global"),
        annotation("region", Location("region", ("slow",), Coordinate(.1), Coordinate(.8),
                                      Amplitude("%", minimum=89.0, maximum=93.0)),
                   "artifact", "Region"),
        annotation("recording", Location("recording"), "quality", "Usable"),
    ], metadata={INTERVAL_LABEL_CATALOG_KEY: [
        {"name": "Artifact", "color": catalog_color},
    ]})
    before = deepcopy(workspace)

    result = build_presentation(recording, workspace, start=0, end=.4)

    assigned = [(item["annotation_id"], item["channel_id"]) for item in result["intervals"]]
    assert assigned == [
        ("local", "fast/channel"), ("global", "fast/channel"),
        ("global", "slow"),
    ]
    assert "subset" not in {item["annotation_id"] for item in result["intervals"]}
    later = build_presentation(recording, workspace, start=.6, end=.85)
    full = build_presentation(recording, workspace)
    later_subset = next(item for item in later["intervals"]
                        if item["annotation_id"] == "subset")
    full_subset = next(item for item in full["intervals"]
                       if item["annotation_id"] == "subset")
    assert later_subset["projection_id"] == full_subset["projection_id"]
    global_items = [item for item in result["intervals"] if item["annotation_id"] == "global"]
    assert len({item["projection_id"] for item in global_items}) == 2
    assert {item["annotation_id"] for item in global_items} == {"global"}
    assert later_subset["color"] == catalog_color
    assert next(item for item in result["intervals"]
                if item["annotation_id"] == "local")["color"] == LEGACY_INTERVAL_COLOR
    assert set(result["manifest"]["intervals"]) == {
        item["projection_id"] for item in result["intervals"]
    }
    assert workspace == before
    assert {item["annotation_id"] for item in result["intervals"]}.isdisjoint(
        {"region", "recording"})


def test_signal_gaps_become_json_null_and_invalid_budget_is_rejected():
    recording = small_recording()
    result = build_presentation(recording, Workspace(recording.id), sample_budget=20)

    assert result["channels"][0]["y"] == [0.0, 1.0, None, 3.0, 4.0]
    json.dumps(result, allow_nan=False)

    for budget in (0, -1, 1.5, True):
        try:
            build_presentation(recording, Workspace(recording.id), sample_budget=budget)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid budget accepted: {budget!r}")


def test_signal_figure_is_valid_stacked_native_plotly_with_stable_channel_identity():
    import plotly.graph_objects as go

    recording = Recording("many", "many.acq", tuple(
        Channel(f"channel-{index}", f"Channel {index}", "mV", 10.0,
                tuple(float(index + sample) for sample in range(101)))
        for index in range(10)
    ))
    presentation = build_presentation(recording, Workspace(recording.id))
    figure = build_signal_figure(presentation)

    go.Figure(figure).to_json(validate=True)
    assert len(figure["data"]) == MAX_RENDERED_CHANNELS
    assert [trace["meta"]["channel_id"] for trace in figure["data"]] == [
        f"channel-{index}" for index in range(MAX_RENDERED_CHANNELS)
    ]
    assert len({trace["uid"] for trace in figure["data"]}) == MAX_RENDERED_CHANNELS
    assert all(trace["type"] == "scattergl" and trace["mode"] == "lines"
               for trace in figure["data"])
    for trace in figure["data"]:
        assert len(trace["customdata"]) == len(trace["x"]) <= 4000
        assert all(item == [trace["meta"]["projection_id"], trace["meta"]["channel_id"], "signal"]
                   for item in trace["customdata"])
    assert figure["layout"]["xaxis8"]["rangeslider"] == {"visible": True}
    assert sum("rangeslider" in figure["layout"]["xaxis" + ("" if index == 1 else str(index))]
               for index in range(1, MAX_RENDERED_CHANNELS + 1)) == 1
    assert all(figure["layout"][f"xaxis{index}"]["matches"] == "x"
               for index in range(2, MAX_RENDERED_CHANNELS + 1))
    assert figure["layout"]["xaxis"]["range"] == [0.0, 8.0]
    assert "points" not in figure and "intervals" not in figure


def test_annotation_figure_uses_identity_bearing_read_only_targets_and_visual_path():
    import plotly.graph_objects as go

    recording = small_recording()
    workspace = Workspace(recording.id, annotations=[
        annotation("maximum", Location("point", ("fast/channel",), Coordinate(.25, 1),
                                        amplitude=Amplitude("mV", 1.0)),
                   "maximum", "Maximum"),
        annotation("minimum", Location("point", ("slow",), Coordinate(.5, 1),
                                        amplitude=Amplitude("%", 91.0)),
                   "minimum", "Minimum"),
        annotation("current", Location("point", ("fast/channel",), Coordinate(.75, 3),
                                        amplitude=Amplitude("mV", 3.0)),
                   "marker", "Current", {"color": MARKER_COLORS[7]}),
        annotation("local", Location("interval", ("fast/channel",), Coordinate(.1),
                                      Coordinate(.5)), "interval", "Local"),
        annotation("subset", Location("interval", ("slow",), Coordinate(.2), Coordinate(.7)),
                   "interval", "Subset"),
        annotation("global", Location("interval", (), Coordinate(.15), Coordinate(.85)),
                   "interval", "Global"),
        annotation("region", Location("region", ("slow",), Coordinate(.1), Coordinate(.8),
                                       Amplitude("%", minimum=89.0, maximum=93.0)),
                   "artifact", "Region"),
    ])
    before = deepcopy(workspace)
    presentation = build_presentation(recording, workspace)
    figure = build_signal_figure(presentation)

    go.Figure(figure).to_json(validate=True)
    assert figure == build_signal_figure(presentation)
    targets = [trace for trace in figure["data"] if trace.get("meta", {}).get("kind")]
    assert {trace["meta"]["kind"] for trace in targets} == {"point", "interval"}
    for trace in targets:
        identity = trace["meta"]
        assert trace["uid"] == identity["projection_id"]
        assert all(row == [identity["projection_id"], identity["annotation_id"],
                           identity["channel_id"], identity["kind"]]
                   for row in trace["customdata"])
        assert "Label:" in trace["hovertemplate"] and "Origin:" in trace["hovertemplate"]

    point_targets = {trace["meta"]["annotation_id"]: trace for trace in targets
                     if trace["meta"]["kind"] == "point"}
    assert point_targets["maximum"]["marker"]["symbol"] == "triangle-up"
    assert point_targets["minimum"]["marker"]["symbol"] == "triangle-down"
    assert point_targets["current"]["marker"] == {
        "color": MARKER_COLORS[7], "symbol": "circle", "size": 10,
        "line": {"color": "#111827", "width": 0},
    }

    global_targets = [trace for trace in targets
                      if trace["meta"]["annotation_id"] == "global"]
    assert len(global_targets) == 2
    assert {trace["meta"]["channel_id"] for trace in global_targets} == {"fast/channel", "slow"}
    assert len({trace["meta"]["projection_id"] for trace in global_targets}) == 2
    assert all(trace["type"] == "scatter" and trace["fill"] == "toself"
               for trace in global_targets)
    assert all("Start: 0.15 s" in trace["hovertemplate"] and
               "End: 0.85 s" in trace["hovertemplate"]
               for trace in global_targets)

    selected = build_signal_figure(
        presentation, {"selected_visual": {"annotation_id": "global", "kind": "interval"}}
    )
    selected_global = [trace for trace in selected["data"]
                       if trace.get("meta", {}).get("annotation_id") == "global"]
    assert all(trace["opacity"] == .34 and trace["line"]["width"] == 2
               for trace in selected_global)
    assert workspace == before


def test_selected_manual_marker_uses_one_native_vertical_time_handle():
    import plotly.graph_objects as go

    recording = small_recording()
    marker = annotation(
        "movable", Location("point", ("slow",), Coordinate(.5, 1),
                             amplitude=Amplitude("%", 91.0)),
        "marker", "Event",
    )
    maximum = annotation(
        "legacy", Location("point", ("fast/channel",), Coordinate(.25, 1),
                            amplitude=Amplitude("mV", 1.0)),
        "maximum", "Maximum",
    )
    presentation = build_presentation(
        recording, Workspace(recording.id, annotations=[marker, maximum]),
    )
    projected = next(point for point in presentation["points"]
                     if point["annotation_id"] == marker.id)
    state = {
        "mode": "navigate",
        "selection": {
            "projection_id": projected["projection_id"],
            "annotation_id": marker.id,
            "channel_id": "slow",
            "kind": "point",
        },
        "selected_visual": {"annotation_id": marker.id, "kind": "point"},
    }
    figure = build_signal_figure(presentation, state)

    go.Figure(figure).to_json(validate=True)
    assert len(figure["layout"]["shapes"]) == 1
    handle = figure["layout"]["shapes"][0]
    assert handle == {
        "type": "line", "x0": .5, "x1": .5, "y0": 0, "y1": 1,
        "xref": "x2", "yref": "y2 domain", "editable": True,
        "layer": "above",
        "name": f"selected-point-time-handle:{projected['projection_id']}",
        "line": {"color": "#d62728", "width": 4, "dash": "dot"},
    }
    assert figure["layout"]["meta"]["shape_by_index"] == {
        "0": {
            "kind": "point-time-handle", "role": "point",
            "projection_id": projected["projection_id"],
            "annotation_id": marker.id, "channel_id": "slow",
        }
    }
    marker_trace = next(trace for trace in figure["data"]
                        if trace.get("meta", {}).get("annotation_id") == marker.id)
    assert marker_trace["type"] == "scatter"
    assert marker_trace["marker"]["symbol"] == "circle"

    assert "shapes" not in build_signal_figure(
        presentation, {**state, "mode": "mark-amplitude"},
    )["layout"]
    legacy = next(point for point in presentation["points"]
                  if point["annotation_id"] == maximum.id)
    legacy_state = {
        **state,
        "selection": {
            "projection_id": legacy["projection_id"],
            "annotation_id": maximum.id,
            "channel_id": "fast/channel",
            "kind": "point",
        },
        "selected_visual": {"annotation_id": maximum.id, "kind": "point"},
    }
    assert "shapes" not in build_signal_figure(presentation, legacy_state)["layout"]
