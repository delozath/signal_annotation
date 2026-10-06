from __future__ import annotations

import importlib.util
from pathlib import Path
import threading

import requests
from werkzeug.serving import make_server


ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = ROOT / ".agents" / "plotly" / "runtime_probe.py"
SPEC = importlib.util.spec_from_file_location("plotly_runtime_probe", PROBE_PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_runtime_versions_figure_and_matched_axes():
    facts = probe.runtime_facts()
    assert facts["plotly_version"] == "7.1.0"
    assert facts["dash_version"] == "4.4.1"
    assert facts["plotly_path"].startswith(str(ROOT / ".venv"))
    assert facts["dash_path"].startswith(str(ROOT / ".venv"))

    figure = probe.build_figure()
    figure.to_json(validate=True)
    assert figure.layout.xaxis2.matches == "x"
    assert figure.layout.shapes[0].editable is True
    assert figure.layout.shapes[0].name == "interval:interval-probe-1:channel-a"
    assert figure.layout.shapes[1].editable is True
    assert figure.layout.shapes[1].name == "point-handle:point-probe-1:channel-b"


def test_relayout_payload_normalization_retains_identity():
    manifest = probe.build_manifest()
    assert probe.normalize_relayout(
        {"shapes[0].x0": 0.75, "shapes[0].y0": -0.5}, manifest
    )["shape_changes"] == [
        {
            "kind": "interval",
            "annotation_id": "interval-probe-1",
            "channel_id": "channel-a",
            "shape_index": 0,
            "field": "x0",
            "value": 0.75,
        },
        {
            "kind": "interval",
            "annotation_id": "interval-probe-1",
            "channel_id": "channel-a",
            "shape_index": 0,
            "field": "y0",
            "value": -0.5,
        },
    ]
    assert probe.normalize_relayout(
        {"shapes[0].x1": 3.25}, manifest
    )["shape_changes"][0]["annotation_id"] == "interval-probe-1"
    point = probe.normalize_relayout(
        {"shapes[1].x0": 2.42, "shapes[1].x1": 2.58}, manifest
    )["shape_changes"]
    assert {change["kind"] for change in point} == {"point-handle"}
    assert {change["channel_id"] for change in point} == {"channel-b"}


def test_dash_callbacks_loopback_and_local_asset():
    app = probe.create_app()
    assert {
        "probe-relayout-output.children",
        "..probe-click-output.children...probe-selection.data..",
        "probe-native-output.children",
    }.issubset(app.callback_map)

    server = make_server("127.0.0.1", 0, app.server, threaded=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        root = requests.get(base_url + "/", timeout=5)
        layout = requests.get(base_url + "/_dash-layout", timeout=5)
        dependencies = requests.get(base_url + "/_dash-dependencies", timeout=5)
        asset = requests.get(base_url + "/assets/probe_events.js", timeout=5)
        assert root.status_code == 200
        assert layout.status_code == 200
        assert dependencies.status_code == 200
        assert asset.status_code == 200
        assert "dash_renderer" in root.text
        assert '"probe-graph"' in layout.text
        assert "probe-relayout-output.children" in dependencies.text
        assert "__signalAnnotationProbe" in asset.text
        assert "https://cdn" not in root.text.lower()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_local_bridge_records_relayout_history_modifiers_and_channel_identity():
    bridge = (PROBE_PATH.parent / "assets" / "probe_events.js").read_text(
        encoding="utf-8"
    )
    assert 'graph.on("plotly_relayout"' in bridge
    assert "shapeByIndex[match[1]]" in bridge
    assert 'kind: "plot-relayout"' in bridge
    assert 'document.getElementById("probe-browser-history")' in bridge
    assert "history.textContent = JSON.stringify(probe.events" in bridge
    assert "nativeEvent.ctrlKey" in bridge
    assert 'const channelByAxis = {y: "channel-a", y2: "channel-b"}' in bridge
    assert "channelByAxis[point && point.yaxis && point.yaxis._id]" in bridge

    figure = probe.build_figure()
    assert figure.data[0].yaxis == "y"
    assert figure.data[2].yaxis == "y2"
    app = probe.create_app()
    history = next(
        component
        for component in app.layout.children
        if getattr(component, "id", None) == "probe-browser-history"
    )
    assert history.children == "[]"
