from __future__ import annotations

import importlib.util
from pathlib import Path
import threading

import requests
from werkzeug.serving import make_server


ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = ROOT / ".agents" / "plotly" / "native_interaction_gate.py"
SPEC = importlib.util.spec_from_file_location("plotly_native_interaction_gate", GATE_PATH)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def test_exact_runtime_and_line_handle_figure_schema():
    facts = gate.runtime_facts()
    assert facts["plotly_version"] == "7.1.0"
    assert facts["dash_version"] == "4.4.1"
    assert facts["plotly_path"].startswith(str(ROOT / ".venv"))
    assert facts["dash_path"].startswith(str(ROOT / ".venv"))

    figure = gate.build_figure()
    figure.to_json(validate=True)
    assert figure.layout.xaxis2.matches == "x"
    assert figure.layout.dragmode == "pan"
    assert gate.NATIVE_CONFIG.keys().isdisjoint({"editable", "edits"})
    assert [shape.type for shape in figure.layout.shapes] == ["rect", "line", "line", "line"]
    assert [shape.editable for shape in figure.layout.shapes] == [False, True, True, True]
    assert [shape.name for shape in figure.layout.shapes] == [
        "interval-band:interval-native-gate-1:channel-a",
        "interval-start-handle:interval-native-gate-1:channel-a",
        "interval-end-handle:interval-native-gate-1:channel-a",
        "point-time-handle:point-native-gate-1:channel-b",
    ]
    assert all(shape.layer == "above" for shape in figure.layout.shapes)
    assert all(shape.y0 == gate.BAND_Y0 and shape.y1 == gate.BAND_Y1 for shape in figure.layout.shapes)
    assert all(shape.x0 == shape.x1 for shape in figure.layout.shapes[1:])


def test_band_and_point_marker_are_not_editable_primitives():
    figure = gate.build_figure()
    assert len(figure.data) == 3
    assert [trace.name for trace in figure.data] == [
        "Channel A signal",
        "Channel B signal",
        "Selected point",
    ]
    point_trace = figure.data[2]
    assert point_trace.mode == "markers"
    assert point_trace.marker.symbol == "circle"
    assert point_trace.customdata == ([gate.POINT_ID, "channel-b", "point"],)
    assert figure.layout.shapes[0].type == "rect"
    assert figure.layout.shapes[0].editable is False
    assert all(shape.type != "circle" for shape in figure.layout.shapes)


def test_manifest_identity_and_x_candidate_normalization_support_variants():
    manifest = gate.build_manifest()
    assert manifest["shape_by_index"]["1"]["role"] == "start"
    assert manifest["shape_by_index"]["2"]["role"] == "end"
    assert manifest["shape_by_index"]["3"]["role"] == "point"
    assert manifest["trace_by_index"]["2"]["annotation_id"] == gate.POINT_ID

    start = gate.normalize_relayout({"shapes[1].x0": 0.75}, manifest)
    end = gate.normalize_relayout({"shapes[2].x1": 3.25}, manifest)
    whole_point = gate.normalize_relayout(
        {"shapes[3]": {"x0": 2.42, "x1": 2.58, "y0": -99.0, "y1": 99.0}}, manifest
    )
    whole_list = gate.normalize_relayout(
        {"shapes": [{}, {"x0": 0.8, "x1": 0.8}]}, manifest
    )
    assert start["native_actions"] == [
        {
            "kind": "interval-edge",
            "role": "start",
            "annotation_id": gate.INTERVAL_ID,
            "channel_id": "channel-a",
            "candidate_time": 0.75,
            "x_fields": {"x0": 0.75},
        }
    ]
    assert end["native_actions"][0]["role"] == "end"
    assert end["native_actions"][0]["candidate_time"] == 3.25
    assert whole_point["native_actions"][0]["role"] == "point"
    assert whole_point["native_actions"][0]["candidate_time"] == 2.5
    assert [entry["field"] for entry in whole_point["ignored_y_fields"]] == ["y0", "y1"]
    assert whole_list["native_actions"][0]["candidate_time"] == 0.8
    unexpected = gate.normalize_relayout({"xaxis.range[0]": 0.5}, manifest)
    assert unexpected["shape_changes"] == []
    assert unexpected["native_actions"] == []


def test_canonical_redraw_ignores_y_noise_keeps_handles_vertical_and_snaps_point():
    manifest = gate.build_manifest()
    initial = gate.initial_canonical_geometry()
    start_event = gate.normalize_relayout(
        {"shapes[1].x0": 0.75, "shapes[1].y0": -80.0, "shapes[1].y1": 80.0}, manifest
    )
    after_start = gate.canonical_after_relayout(initial, start_event)
    assert after_start["interval"] == {"start": 0.75, "end": 3.0}
    assert after_start["revision"] == 1

    end_event = gate.normalize_relayout({"shapes[2].x0": 3.3, "shapes[2].x1": 3.3}, manifest)
    after_end = gate.canonical_after_relayout(after_start, end_event)
    assert after_end["interval"] == {"start": 0.75, "end": 3.3}

    point_event = gate.normalize_relayout(
        {"shapes[3].x0": 2.47, "shapes[3].x1": 2.47, "shapes[3].y0": -7.0, "shapes[3].y1": 7.0}, manifest
    )
    after_point = gate.canonical_after_relayout(after_end, point_event)
    assert after_point["point"]["time"] == 2.45
    assert after_point["revision"] == 3

    y_only_event = gate.normalize_relayout(
        {"shapes[3].y0": -70.0, "shapes[3].y1": 70.0}, manifest
    )
    after_y_only = gate.canonical_after_relayout(after_point, y_only_event)
    assert after_y_only["point"]["time"] == 2.45
    assert after_y_only["interval"] == {"start": 0.75, "end": 3.3}
    assert after_y_only["revision"] == 4

    figure = gate.build_figure(after_y_only)
    band, start_line, end_line, point_line = figure.layout.shapes
    assert (band.x0, band.x1, band.y0, band.y1, band.editable) == (0.75, 3.3, gate.BAND_Y0, gate.BAND_Y1, False)
    assert (start_line.x0, start_line.x1, start_line.y0, start_line.y1) == (0.75, 0.75, gate.BAND_Y0, gate.BAND_Y1)
    assert (end_line.x0, end_line.x1, end_line.y0, end_line.y1) == (3.3, 3.3, gate.BAND_Y0, gate.BAND_Y1)
    assert (point_line.x0, point_line.x1, point_line.y0, point_line.y1) == (2.45, 2.45, gate.BAND_Y0, gate.BAND_Y1)
    assert tuple(figure.data[2].x) == (2.45,)


def test_dash_callback_ownership_loopback_local_asset_and_copyable_history():
    app = gate.create_app()
    graph_callback = app.callback_map["native-gate-graph.figure"]
    assert graph_callback["inputs"] == [{"id": "native-gate-canonical", "property": "data"}]
    assert all(
        not (
            any(item["id"] == "native-gate-graph" and item["property"] == "relayoutData" for item in callback["inputs"])
            and str(callback["output"]) == "native-gate-graph.figure"
        )
        for callback in app.callback_map.values()
    )
    assert any(
        any(item["id"] == "native-gate-graph" and item["property"] == "relayoutData" for item in callback["inputs"])
        and "native-gate-canonical.data" in str(callback["output"])
        for callback in app.callback_map.values()
    )
    history = next(component for component in app.layout.children if getattr(component, "id", None) == "native-gate-browser-history")
    assert history.children == "[]"
    assert history.readOnly is True

    server = make_server("127.0.0.1", 0, app.server, threaded=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        root = requests.get(base_url + "/", timeout=5)
        layout = requests.get(base_url + "/_dash-layout", timeout=5)
        dependencies = requests.get(base_url + "/_dash-dependencies", timeout=5)
        asset = requests.get(base_url + "/assets/native_gate_events.js", timeout=5)
        assert all(response.status_code == 200 for response in (root, layout, dependencies, asset))
        assert "dash_renderer" in root.text
        assert '"native-gate-graph"' in layout.text
        assert "native-gate-canonical.data" in dependencies.text
        assert "native-gate-graph.figure" in dependencies.text
        assert "https://cdn" not in root.text.lower()
        assert "__signalAnnotationNativeGate" in asset.text
        assert 'history.value = JSON.stringify(gate.events' in asset.text
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_asset_records_every_native_event_without_geometry_mutation():
    bridge = (GATE_PATH.parent / "assets" / "native_gate_events.js").read_text(encoding="utf-8")
    assert 'graph.on("plotly_relayout"' in bridge
    assert 'kind: "native-plotly-relayout"' in bridge
    assert 'role: "start"' in bridge
    assert 'role: "end"' in bridge
    assert 'role: "point"' in bridge
    assert "shapeByIndex[match[1]]" in bridge
    assert "history.value = JSON.stringify(gate.events" in bridge
    assert 'publish({kind: "native-plotly-relayout", raw: eventData, shape_changes: shapeChanges});' in bridge
    assert "Plotly.relayout" not in bridge
    assert "getBoundingClientRect" not in bridge
    assert ".style.left" not in bridge
    assert ".style.top" not in bridge
