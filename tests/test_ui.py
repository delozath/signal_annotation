import json
from copy import deepcopy
from dataclasses import replace
import pytest
from bokeh.document import Document
from bokeh.events import SelectionGeometry, Tap
from bokeh.core.validation import check_integrity
from bokeh.models import Div, PointDrawTool, Rect, TapTool
from signal_annotation.application import (AnnotationService, LEGACY_INTERVAL_COLOR,
                                           MARKER_COLORS)
from signal_annotation.adapters.bokeh_ui import build_document
from signal_annotation.adapters.readers import demo_recording
from signal_annotation.adapters.storage import JsonSidecarRepository
from signal_annotation.domain import (Amplitude, Coordinate, Label, Location, Proposal,
                                      Reference, Relationship, ValidationError)
from signal_annotation.validation import (
    AMPLITUDE_LABEL_CATALOG_KEY, AMPLITUDE_LABEL_COLOR_ATTRIBUTE,
    AMPLITUDE_LABEL_NAME_ATTRIBUTE, GENERAL_NOTES_KEY, INTERVAL_LABEL_CATALOG_KEY,
    INTERVAL_LABEL_COLOR_ATTRIBUTE, INTERVAL_LABEL_NAME_ATTRIBUTE,
)


def viewer(tmp_path):
    recording = demo_recording()
    path = tmp_path / 'demo.annotations.json'
    service = AnnotationService(recording, JsonSidecarRepository(path, recording), 'test-ui')
    return build_document(Document(), service, path)


def test_build_serializes_without_validation_warnings(tmp_path):
    v = viewer(tmp_path)
    issues = check_integrity(v.doc.models)
    assert not issues.error and not issues.warning
    assert len(v.plots) == 6
    assert all(p.x_range is v.x_range for p in v.plots.values())
    assert v.doc.get_model_by_name('time_scrollbar') is v.time_scrollbar
    assert v.time_scrollbar.value == (0, min(8, v.recording.duration))
    assert v.doc.get_model_by_name('undo') is None
    assert v.doc.get_model_by_name('delete') is None
    assert v.doc.get_model_by_name('delete_non_points') is None
    assert v.doc.get_model_by_name('delete_marker') is None
    assert v.doc.get_model_by_name('channel') is None
    assert not hasattr(v, 'channel_select')
    assert v.doc.get_model_by_name('label') is None
    assert not hasattr(v, 'label')
    assert v.doc.get_model_by_name('load_edit') is None
    assert not hasattr(v, 'load_selected')
    assert v.doc.get_model_by_name('edit') is None
    assert not hasattr(v, 'edit_selected')
    assert not hasattr(v, 'selected_annotation')
    assert v.doc.get_model_by_name('annotations_source') is None
    assert v.doc.get_model_by_name('annotations_table') is None
    assert not hasattr(v, 'table_source')
    assert not hasattr(v, 'empty_table')
    assert all('Saved annotations' not in model.text
               for model in v.doc.select({'type': Div}))
    assert v.active_channel_id == v.recording.channels[0].id
    assert v.delete_events.data == {'sequence': [0], 'selected_id': [''], 'selected_count': [0]}
    assert all(isinstance(v.point_tap_tools[channel_id], TapTool)
               and plot.toolbar.active_tap is v.point_tap_tools[channel_id]
               and v.point_tap_tools[channel_id].renderers == [
                   v.point_draw_tools[channel_id].renderers[0],
                   v.interval_renderers[channel_id]]
               for channel_id, plot in v.plots.items())
    assert all(isinstance(tool, PointDrawTool) and not tool.add and tool.drag
               and tool.visible
               for tool in v.point_draw_tools.values())
    assert all(isinstance(tool, PointDrawTool) and not tool.add and tool.drag and tool.visible
               and len(tool.renderers) == 2
               for tool in v.interval_edge_draw_tools.values())
    assert all(isinstance(renderer.glyph, Rect) and renderer.glyph.width == 12
               and renderer.glyph.width_units == 'screen'
               and renderer.glyph.height == 'height'
               and renderer.glyph.height_units == 'data'
               for tool in v.interval_edge_draw_tools.values()
               for renderer in tool.renderers)
    assert all(tools['start'] is tools['end'] is v.interval_edge_draw_tools[channel_id]
               for channel_id, tools in v.interval_draw_tools.items())
    assert all(v.interval_handle_sources[channel_id]['start'] is not v.sources[channel_id][1]
               and v.interval_handle_sources[channel_id]['end'] is not v.sources[channel_id][1]
               for channel_id in v.plots)
    assert all(source.data == {'id': [], 'x': [], 'y': [], 'height': [], 'label': [],
                               'color': [], 'origin': []}
               for handles in v.interval_handle_sources.values() for source in handles.values())
    assert all(source.data == {'id': [], 'left': [], 'right': [], 'bottom': [], 'top': [],
                               'label': [], 'color': [], 'origin': []}
               for _, _, source, _ in v.sources.values())
    assert all(renderer.selection_glyph.fill_color == '#ffd166'
               and renderer.selection_glyph.line_width == 3
               and renderer.nonselection_glyph.fill_alpha == .07
               for renderer in v.interval_renderers.values())
    assert v.doc.to_json() is not None


def test_interval_edge_tool_is_preactivated_with_visible_edges_and_point_wins(tmp_path):
    v = viewer(tmp_path)

    for channel_id, plot in v.plots.items():
        assert not plot.js_event_callbacks.get('mousemove')
        assert plot.toolbar.active_drag is v.pan_tools[channel_id]
        assert plot.toolbar.active_tap is v.point_tap_tools[channel_id]
    assert not hasattr(v, 'interval_edge_hover_callbacks')

    v.service.add('interval', 'ch0', 1., 2., 'interval')
    v.service.add('maximum', 'ch0', .12, None, 'point')
    v.refresh()
    plot = v.plots['ch0']
    assert plot.toolbar.active_drag is v.interval_edge_draw_tools['ch0']
    assert plot.toolbar.active_tap is v.point_tap_tools['ch0']
    assert all(other.toolbar.active_drag is v.pan_tools[channel_id]
               for channel_id, other in v.plots.items() if channel_id != 'ch0')

    v.sources['ch0'][1].selected.indices = [0]
    assert plot.toolbar.active_drag is v.point_draw_tools['ch0']
    assert plot.toolbar.active_tap is v.point_draw_tools['ch0']
    v.sources['ch0'][1].selected.indices = []
    assert plot.toolbar.active_drag is v.interval_edge_draw_tools['ch0']
    assert plot.toolbar.active_tap is v.point_tap_tools['ch0']


def test_manual_workflow_is_hidden_in_reopenable_help_dialog(tmp_path):
    v = viewer(tmp_path)

    assert v.doc.get_model_by_name('help') is v.help_button
    assert v.doc.get_model_by_name('help_dialog') is v.help_dialog
    assert v.help_dialog.close_action == 'hide'
    assert not v.help_dialog.visible
    assert 'Manual workflow' in v.help_dialog.content.text
    assert 'select one point or interval' in v.help_dialog.content.text

    v.open_help()
    assert v.help_dialog.visible
    v.help_dialog.visible = False
    v.open_help()
    assert v.help_dialog.visible


def test_general_recording_notes_save_clear_and_reload(tmp_path):
    v = viewer(tmp_path)
    notes = "First observation\nSecond observation\n  Exact free text"
    assert v.doc.get_model_by_name('general_notes') is v.general_notes
    assert v.doc.get_model_by_name('save_general_notes') is not None
    assert v.doc.get_model_by_name('clear_general_notes') is not None

    v.general_notes.value = notes
    v.run(v.save_general_notes)
    raw = json.loads(v.sidecar.read_text())
    assert raw['metadata'][GENERAL_NOTES_KEY] == notes
    assert v.general_notes.value == notes

    reopened = viewer(tmp_path)
    assert reopened.general_notes.value == notes
    reopened.run(reopened.clear_general_notes)
    assert reopened.general_notes.value == ''
    assert json.loads(reopened.sidecar.read_text())['metadata'][GENERAL_NOTES_KEY] == ''


def test_point_interval_delete_reload_csv(tmp_path):
    v = viewer(tmp_path)
    assert v.sidecar.is_file()
    assert not json.loads(v.sidecar.read_text())['annotations']
    v.amplitude_label_name.value = 'systole'
    v.run(v.create_amplitude_label)
    v.mode.value = 'marker'
    v.tap(Tap(model=v.plots['ch0'], x=.2411, y=1), 'ch0')
    a = v.service.workspace.annotations[0]
    assert a.start == .242 and a.location.start.sample_index == 121
    assert (a.label.code, a.label.text) == ('marker', 'systole')
    assert 'JSON saved' in v.status.text
    v.interval_label_name.value = 'artifact'
    v.run(v.create_interval_label)
    v.mode.value = 'interval'
    v.selection(SelectionGeometry(model=v.plots['ch1'], geometry={'type':'rect','x0':3.,'x1':1.}, final=True), 'ch1')
    assert len(v.service.workspace.annotations) == 2
    assert v.service.workspace.annotations[1].location.channel_ids == ('ch1',)
    assert v.sources['ch1'][2].data['left'] == [1.]
    assert all(not sources[2].data['left'] for channel_id, sources in v.sources.items()
               if channel_id != 'ch1')
    v.sources['ch0'][1].selected.indices = [0]
    v.delete_events.patch({'sequence': [(0, 1)], 'selected_id': [(0, a.id)],
                           'selected_count': [(0, 1)]})
    assert len(v.service.workspace.annotations) == 1
    v.run(v.service.reload)
    assert len(v.service.workspace.annotations) == 1
    v.export()
    assert v.sidecar.with_suffix('.csv').is_file()
    assert 'Current CSV exported' in v.status.text
    raw = json.loads(v.sidecar.read_text())
    assert 'revision' not in raw and 'audit' not in raw and 'history' not in raw


def test_plot_point_delete_persists_without_table_delete_path(tmp_path):
    v = viewer(tmp_path)
    point = v.service.add('minimum', 'ch0', .1234, None, 'selected point')
    interval = v.service.add('interval', 'ch1', 1, 2, 'kept interval')
    v.refresh()

    assert v.sources['ch0'][1].data['id'] == [point.id]
    assert not hasattr(v, 'delete_selected_non_points')
    assert v.doc.get_model_by_name('delete_marker') is None

    v.sources['ch0'][1].selected.indices = [0]
    v.delete_events.patch({'sequence': [(0, 1)], 'selected_id': [(0, point.id)],
                           'selected_count': [(0, 1)]})
    assert [a.id for a in v.service.workspace.annotations] == [interval.id]
    assert [a['id'] for a in json.loads(v.sidecar.read_text())['annotations']] == [interval.id]
    assert v.sources['ch0'][1].selected.indices == []

    reopened = viewer(tmp_path)
    assert [a.id for a in reopened.service.workspace.annotations] == [interval.id]


def test_keyboard_delete_without_one_valid_plot_selection_is_safe(tmp_path):
    v = viewer(tmp_path)
    point = v.service.add('maximum', 'ch0', .24, None, 'point')
    v.refresh()
    before = v.sidecar.read_bytes()

    v.delete_events.patch({'sequence': [(0, 1)], 'selected_id': [(0, '')],
                           'selected_count': [(0, 0)]})
    assert v.sidecar.read_bytes() == before
    assert 'Select exactly one point or interval' in v.status.text

    v.sources['ch0'][1].selected.indices = [99]
    v.delete_events.patch({'sequence': [(0, 2)], 'selected_id': [(0, '')],
                           'selected_count': [(0, 0)]})
    assert v.sidecar.read_bytes() == before
    assert [a.id for a in v.service.workspace.annotations] == [point.id]


def test_interval_selection_is_singular_and_one_global_copy_resolves_one_identity(tmp_path):
    v = viewer(tmp_path)
    local = v.service.add('interval', 'ch0', 1., 2., 'local interval')
    global_interval = v.service.add('interval', None, 3., 4., 'global interval')
    point = v.service.add('marker', 'ch1', .12, None, 'point')
    v.refresh()

    v.sources['ch1'][1].selected.indices = [0]
    assert v.sources['ch1'][1].data['id'] == [point.id]
    global_index = v.sources['ch3'][2].data['id'].index(global_interval.id)
    v.sources['ch3'][2].selected.indices = [global_index]

    assert all(not source.selected.indices for _, source, _, _ in v.sources.values())
    for channel_id, (_, _, source, _) in v.sources.items():
        selected_ids = [source.data['id'][i] for i in source.selected.indices]
        assert selected_ids == ([global_interval.id] if channel_id == 'ch3' else [])
    assert v.plots['ch3'].toolbar.active_drag is v.interval_edge_draw_tools['ch3']
    assert v.plots['ch3'].toolbar.active_tap is v.point_tap_tools['ch3']
    assert all(plot.toolbar.active_drag is v.interval_edge_draw_tools[channel_id]
               and plot.toolbar.active_tap is v.point_tap_tools[channel_id]
               for channel_id, plot in v.plots.items() if channel_id != 'ch3')
    assert 'drag either vertical edge' in v.status.text

    local_index = v.sources['ch0'][2].data['id'].index(local.id)
    v.sources['ch0'][2].selected.indices = [local_index]
    assert [v.sources['ch0'][2].data['id'][i]
            for i in v.sources['ch0'][2].selected.indices] == [local.id]
    assert all(not source.selected.indices for channel_id, (_, _, source, _) in v.sources.items()
               if channel_id != 'ch0')
    assert v.plots['ch0'].toolbar.active_drag is v.interval_edge_draw_tools['ch0']
    assert v.plots['ch0'].toolbar.active_tap is v.point_tap_tools['ch0']

    callbacks = v.sources['ch0'][2].selected.js_property_callbacks['change:indices']
    assert any('source === interval_source ? [index] : []' in callback.code
               and 'const selected_ids = new Set()' in callback.code
               for callback in callbacks)


def test_plot_interval_delete_deduplicates_global_copies_and_persists(tmp_path):
    v = viewer(tmp_path)
    interval = v.service.add('interval', None, 1., 2., 'global interval')
    point = v.service.add('maximum', 'ch0', .12, None, 'kept point')
    v.refresh()

    v.sources['ch2'][2].selected.indices = [0]
    assert v.sources['ch2'][2].selected.indices == [0]
    assert all(not source.selected.indices for channel_id, (_, _, source, _) in v.sources.items()
               if channel_id != 'ch2')
    v.delete_selected_annotation()

    assert v.service.workspace.annotations == [point]
    assert [item['id'] for item in json.loads(v.sidecar.read_text())['annotations']] == [point.id]
    assert all(interval.id not in source.data['id'] for _, _, source, _ in v.sources.values())
    reopened = viewer(tmp_path)
    assert reopened.service.workspace.annotations == [point]


def test_interval_delete_rejects_multiple_or_related_selection_without_writing(tmp_path):
    v = viewer(tmp_path)
    point = v.service.add('maximum', 'ch0', .12, None, 'point')
    interval = v.service.add('interval', 'ch0', 1., 2., 'protected interval')
    candidate = deepcopy(v.service.workspace)
    candidate.relationships = [Relationship(
        'rel-interval', Reference('annotation', interval.id), Reference('channel', 'ch0'),
        Label('contains', 'contains'))]
    v.service.repository.save(candidate)
    v.service.reload()
    v.refresh()
    before = v.sidecar.read_bytes()

    v.syncing_annotation_selection = True
    v.sources['ch0'][1].selected.indices = [0]
    v.sources['ch0'][2].selected.indices = [0]
    v.syncing_annotation_selection = False
    v.delete_selected_annotation()
    assert v.sidecar.read_bytes() == before
    assert 'Select exactly one point or interval' in v.status.text

    v.sources['ch0'][1].selected.indices = []
    v.sources['ch0'][2].selected.indices = [0]
    v.delete_events.patch({'sequence': [(0, 1)], 'selected_id': [(0, interval.id)],
                           'selected_count': [(0, 1)]})
    assert v.sidecar.read_bytes() == before
    assert [item.id for item in v.service.workspace.annotations] == [point.id, interval.id]
    assert 'relationships' in v.status.text


def test_selecting_point_in_navigate_fully_activates_draw_and_clear_restores_tools(tmp_path):
    v = viewer(tmp_path)
    v.service.add('maximum', 'ch0', .12, None, 'movable point')
    v.refresh()
    source = v.sources['ch0'][1]
    plot = v.plots['ch0']

    assert plot.toolbar.active_drag is v.pan_tools['ch0']
    assert plot.toolbar.active_tap is v.point_tap_tools['ch0']
    assert v.point_draw_tools['ch0'].visible
    callbacks = source.selected.js_property_callbacks['change:indices']
    assert any('point_plots[i].toolbar.active_tap = can_move_point' in callback.code
               and 'point_plots[i].toolbar.active_drag = active_drag != null' in callback.code
               and 'point_draw_tools[i].visible = is_navigate' in callback.code
               for callback in callbacks)
    assert v.mode.js_property_callbacks['change:value']

    source.selected.indices = [0]
    assert plot.toolbar.active_drag is v.point_draw_tools['ch0']
    assert plot.toolbar.active_tap is v.point_draw_tools['ch0']
    assert 'Drag it to move it' in v.status.text

    source.selected.indices = []
    assert plot.toolbar.active_drag is v.pan_tools['ch0']
    assert plot.toolbar.active_tap is v.point_tap_tools['ch0']

    source.selected.indices = [0]
    v.mode.value = 'interval'
    assert source.selected.indices == []
    assert plot.toolbar.active_drag is v.sources['ch0'][3]
    assert plot.toolbar.active_tap is v.point_tap_tools['ch0']
    assert not v.point_draw_tools['ch0'].visible

    source.selected.indices = [0]
    assert source.selected.indices == []
    assert plot.toolbar.active_drag is v.sources['ch0'][3]
    assert plot.toolbar.active_tap is v.point_tap_tools['ch0']

    v.mode.value = 'marker'
    assert not v.point_draw_tools['ch0'].visible
    assert plot.toolbar.active_drag is v.pan_tools['ch0']
    assert plot.toolbar.active_tap is v.point_tap_tools['ch0']


def test_drag_point_snaps_one_annotation_and_persists_immediately(tmp_path):
    v = viewer(tmp_path)
    point = v.service.add('maximum', 'ch0', .12, None, 'dragged')
    other = v.service.add('minimum', 'ch1', .5, None, 'untouched')
    v.refresh()
    source = v.sources['ch0'][1]
    source.selected.indices = [0]
    assert v.plots['ch0'].toolbar.active_drag is v.point_draw_tools['ch0']
    assert v.plots['ch0'].toolbar.active_tap is v.point_draw_tools['ch0']
    changed = dict(source.data)
    changed['x'] = [.2471]
    changed['y'] = [999.]
    source.data = changed

    sample_index, seconds, value = v.recording.channel('ch0').sample_at(.2471)
    moved = v.service.workspace.annotations[0]
    assert moved.id == point.id
    assert moved.label == point.label
    assert moved.provenance == point.provenance
    assert (moved.location.start.sample_index, moved.start, moved.value) == (sample_index, seconds, value)
    assert v.service.workspace.annotations[1] == other
    raw = json.loads(v.sidecar.read_text())['annotations']
    assert [item['id'] for item in raw] == [point.id, other.id]
    assert raw[0]['location']['start'] == {'seconds': seconds, 'sample_index': sample_index}
    assert raw[0]['location']['amplitude']['value'] == value
    assert source.data['x'] == [seconds]
    assert source.data['y'] == [value]
    assert source.selected.indices == []
    assert v.plots['ch0'].toolbar.active_drag is v.pan_tools['ch0']
    assert v.plots['ch0'].toolbar.active_tap is v.point_tap_tools['ch0']
    assert 'JSON saved' in v.status.text


def test_point_move_is_rejected_outside_navigate_without_json_change(tmp_path):
    v = viewer(tmp_path)
    point = v.service.add('maximum', 'ch0', .12, None, 'stationary')
    v.refresh()
    v.mode.value = 'marker'
    source = v.sources['ch0'][1]
    before = v.sidecar.read_bytes()

    changed = dict(source.data)
    changed['x'] = [.25]
    changed['y'] = [999.]
    source.data = changed

    assert v.service.workspace.annotations[0] == point
    assert v.sidecar.read_bytes() == before
    assert source.data['x'] == [point.start]
    assert source.data['y'] == [point.value]
    assert 'Choose Navigate' in v.status.text


def test_invalid_drag_restores_renderer_without_memory_or_disk_change(tmp_path):
    v = viewer(tmp_path)
    point = v.service.add('maximum', 'ch0', .12, None, 'point')
    v.refresh()
    before_bytes = v.sidecar.read_bytes()
    before_annotation = v.service.workspace.annotations[0]
    source = v.sources['ch0'][1]
    changed = dict(source.data)
    changed['x'] = [-1.]
    changed['y'] = [123.]
    source.data = changed

    assert v.sidecar.read_bytes() == before_bytes
    assert v.service.workspace.annotations == [before_annotation]
    assert source.data['id'] == [point.id]
    assert source.data['x'] == [point.start]
    assert source.data['y'] == [point.value]
    assert 'could not be completed' in v.status.text


def test_drag_each_interval_endpoint_persists_and_refreshes_all_global_copies(tmp_path):
    v = viewer(tmp_path)
    interval = v.service.add('interval', None, 1., 2., 'global interval')
    point = v.service.add('marker', 'ch0', .12, None, 'untouched point')
    v.refresh()

    start_source = v.interval_handle_sources['ch2']['start']
    assert not start_source.selected.indices
    assert not v.sources['ch2'][2].selected.indices
    assert v.plots['ch2'].toolbar.active_tap is v.point_tap_tools['ch2']
    assert v.plots['ch2'].toolbar.active_drag is v.interval_edge_draw_tools['ch2']
    assert [renderer.data_source for renderer in v.interval_edge_draw_tools['ch2'].renderers] == [
        start_source, v.interval_handle_sources['ch2']['end']]
    assert start_source.data['height'] == [
        v.plots['ch2'].y_range.end - v.plots['ch2'].y_range.start]
    changed = dict(start_source.data)
    changed['x'] = [.5]
    changed['y'] = [999.]
    start_source.data = changed

    resized_start = v.service.workspace.annotations[0]
    assert resized_start == replace(interval,
        location=replace(interval.location, start=Coordinate(.5)))
    assert v.service.workspace.annotations[1] == point
    assert start_source.selected.indices == []
    assert v.plots['ch2'].toolbar.active_tap is v.point_tap_tools['ch2']
    assert v.plots['ch2'].toolbar.active_drag is v.interval_edge_draw_tools['ch2']
    for channel_id, handles in v.interval_handle_sources.items():
        assert handles['start'].data['id'] == [interval.id]
        assert handles['start'].data['x'] == [.5]
        expected_y = (v.plots[channel_id].y_range.start + v.plots[channel_id].y_range.end) / 2
        assert handles['start'].data['y'] == [expected_y]
        assert handles['end'].data['x'] == [2.]

    end_source = v.interval_handle_sources['ch4']['end']
    assert not end_source.selected.indices
    assert not v.sources['ch4'][2].selected.indices
    assert v.plots['ch4'].toolbar.active_tap is v.point_tap_tools['ch4']
    assert v.plots['ch4'].toolbar.active_drag is v.interval_edge_draw_tools['ch4']
    changed = dict(end_source.data)
    changed['x'] = [2.5]
    changed['y'] = [-999.]
    end_source.data = changed

    resized_end = v.service.workspace.annotations[0]
    assert resized_end == replace(resized_start,
        location=replace(resized_start.location, end=Coordinate(2.5)))
    raw = json.loads(v.sidecar.read_text())['annotations']
    assert [item['id'] for item in raw] == [interval.id, point.id]
    assert raw[0]['location']['start'] == {'seconds': .5, 'sample_index': None}
    assert raw[0]['location']['end'] == {'seconds': 2.5, 'sample_index': None}
    assert all(source.data['right'] == [2.5] for _, _, source, _ in v.sources.values())

    reopened = viewer(tmp_path)
    assert reopened.service.workspace.annotations == [resized_end, point]
    assert all(handles['start'].data['x'] == [.5] and handles['end'].data['x'] == [2.5]
               for handles in reopened.interval_handle_sources.values())


def test_selecting_interval_handle_in_navigate_activates_endpoint_and_wins_selection(tmp_path):
    v = viewer(tmp_path)
    interval = v.service.add('interval', 'ch0', 1., 2., 'interval')
    v.service.add('maximum', 'ch0', .12, None, 'point')
    v.refresh()
    start = v.interval_handle_sources['ch0']['start']
    end = v.interval_handle_sources['ch0']['end']
    plot = v.plots['ch0']

    v.sources['ch0'][1].selected.indices = [0]
    v.sources['ch0'][2].selected.indices = [0]
    start.selected.indices = [0]
    assert start.data['id'] == [interval.id]
    assert start.selected.indices == [0]
    assert end.selected.indices == []
    assert v.sources['ch0'][1].selected.indices == []
    assert v.sources['ch0'][2].selected.indices == []
    assert plot.toolbar.active_tap is v.point_tap_tools['ch0']
    assert plot.toolbar.active_drag is v.interval_edge_draw_tools['ch0']
    assert [renderer.data_source for renderer in v.interval_edge_draw_tools['ch0'].renderers] == [
        start, end]
    assert 'Drag it to resize' in v.status.text

    v.sources['ch0'][2].selected.indices = [0]
    assert v.sources['ch0'][2].selected.indices == []
    assert start.selected.indices == [0]

    end.selected.indices = [0]
    assert start.selected.indices == []
    assert end.selected.indices == [0]
    assert plot.toolbar.active_tap is v.point_tap_tools['ch0']
    assert plot.toolbar.active_drag is v.interval_edge_draw_tools['ch0']

    end.selected.indices = []
    assert plot.toolbar.active_tap is v.point_tap_tools['ch0']
    assert plot.toolbar.active_drag is v.interval_edge_draw_tools['ch0']

    callbacks = start.selected.js_property_callbacks['change:indices']
    assert any('const winning_handle' in callback.code
               and 'const has_visible_edge' in callback.code
               and 'edge_draw_tools[i].visible = is_navigate' in callback.code
               for callback in callbacks)


def test_interval_handle_drag_is_disabled_and_rolled_back_outside_navigate(tmp_path):
    v = viewer(tmp_path)
    interval = v.service.add('interval', 'ch0', 1., 2., 'interval')
    v.refresh()
    source = v.interval_handle_sources['ch0']['start']
    before = v.sidecar.read_bytes()

    v.mode.value = 'interval'
    assert not v.interval_draw_tools['ch0']['start'].visible
    assert not v.interval_draw_tools['ch0']['end'].visible
    source.selected.indices = [0]
    assert source.selected.indices == []
    changed = dict(source.data)
    changed['x'] = [.5]
    source.data = changed

    assert v.service.workspace.annotations == [interval]
    assert v.sidecar.read_bytes() == before
    assert source.data['x'] == [1.]
    assert v.plots['ch0'].toolbar.active_tap is v.point_tap_tools['ch0']
    assert v.plots['ch0'].toolbar.active_drag is v.sources['ch0'][3]
    assert 'Choose Navigate' in v.status.text


def test_interval_handle_vertical_drag_is_ignored_and_redrawn_without_save(tmp_path):
    v = viewer(tmp_path)
    interval = v.service.add('interval', 'ch0', 1., 2., 'interval')
    v.refresh()
    before_workspace, before_bytes = deepcopy(v.service.workspace), v.sidecar.read_bytes()
    source = v.interval_handle_sources['ch0']['start']
    changed = dict(source.data)
    changed['y'] = [999.]
    source.data = changed

    expected_y = (v.plots['ch0'].y_range.start + v.plots['ch0'].y_range.end) / 2
    assert v.service.workspace == before_workspace
    assert v.service.workspace.annotations == [interval]
    assert v.sidecar.read_bytes() == before_bytes
    assert source.data['x'] == [1.]
    assert source.data['y'] == [expected_y]


def test_invalid_multiple_and_foreign_interval_drags_roll_back_without_writing(tmp_path):
    v = viewer(tmp_path)
    first = v.service.add('interval', 'ch0', 1., 2., 'first')
    second = v.service.add('interval', 'ch0', 3., 4., 'second')
    foreign = v.service.add('interval', 'ch1', 1., 2., 'foreign')
    v.refresh()
    before_workspace, before_bytes = deepcopy(v.service.workspace), v.sidecar.read_bytes()
    source = v.interval_handle_sources['ch0']['start']

    changed = dict(source.data)
    changed['x'] = [2., 3.]
    source.data = changed
    assert 'start &lt; end' in v.status.text
    assert v.service.workspace == before_workspace and v.sidecar.read_bytes() == before_bytes
    assert source.data['id'] == [first.id, second.id] and source.data['x'] == [1., 3.]

    changed = dict(source.data)
    changed['x'] = [.5, 2.5]
    source.data = changed
    assert 'Resize exactly one interval endpoint' in v.status.text
    assert v.service.workspace == before_workspace and v.sidecar.read_bytes() == before_bytes
    assert source.data['x'] == [1., 3.]

    changed = dict(source.data)
    changed['id'] = [foreign.id, second.id]
    changed['x'] = [.5, 3.]
    source.data = changed
    assert 'not a current annotation on this channel' in v.status.text
    assert v.service.workspace == before_workspace and v.sidecar.read_bytes() == before_bytes
    assert source.data['id'] == [first.id, second.id] and source.data['x'] == [1., 3.]


def test_model_reviewed_interval_handle_drag_is_rejected_and_rolled_back(tmp_path):
    v = viewer(tmp_path)
    proposal = Proposal('proposal-interval-resize',
        Location('interval', ('ch0',), Coordinate(1.), Coordinate(2.)),
        Label('suggested_artifact', 'suggested interval'), 'future-model', 'v1', .9,
        '2026-10-04T00:00:00+00:00')
    v.service.register_proposals([proposal])
    v.service.review_proposal(proposal.id, True)
    v.refresh()
    reviewed = v.service.workspace.annotations[0]
    before_workspace, before_bytes = deepcopy(v.service.workspace), v.sidecar.read_bytes()
    source = v.interval_handle_sources['ch0']['end']
    changed = dict(source.data)
    changed['x'] = [2.5]
    changed['y'] = [999.]
    source.data = changed

    assert 'Only manual interval annotations can be resized' in v.status.text
    assert v.service.workspace == before_workspace
    assert v.sidecar.read_bytes() == before_bytes
    assert source.data['id'] == [reviewed.id]
    assert source.data['x'] == [2.]


def test_invalid_action_survives_and_does_not_write(tmp_path):
    v = viewer(tmp_path)
    initial = v.sidecar.read_bytes()
    v.run(v.add_numeric)
    assert not v.service.workspace.annotations and 'could not be completed' in v.status.text
    v.mode.value = 'marker'
    v.amplitude_label_name.value = 'named marker'
    v.run(v.create_amplitude_label)
    after_label = v.sidecar.read_bytes()
    v.start.value = -1
    v.run(v.add_numeric)
    assert v.sidecar.read_bytes() == after_label
    v.start.value = .1234
    v.run(v.add_numeric)
    assert len(v.service.workspace.annotations) == 1


def test_named_marker_requires_a_catalog_selection_and_persists_it_immediately(tmp_path):
    v = viewer(tmp_path)
    assert v.doc.get_model_by_name('amplitude_label_name') is v.amplitude_label_name
    assert v.doc.get_model_by_name('amplitude_label_select') is v.amplitude_label_select
    assert v.doc.get_model_by_name('add_amplitude_label') is not None
    assert v.amplitude_label_select.options == [('', 'Choose a registered label')]
    v.mode.value = 'marker'
    before = v.sidecar.read_bytes()

    v.tap(Tap(model=v.plots['ch0'], x=.1234, y=1), 'ch0')
    assert v.service.workspace.annotations == []
    assert v.sidecar.read_bytes() == before
    assert 'Choose a registered amplitude label' in v.status.text

    v.amplitude_label_name.value = '  inspiration peak  '
    v.run(v.create_amplitude_label)
    v.tap(Tap(model=v.plots['ch0'], x=.1234, y=1), 'ch0')
    annotation = v.service.workspace.annotations[0]
    raw = json.loads(v.sidecar.read_text())['annotations'][0]
    assert (annotation.label.code, annotation.label.text) == ('marker', 'inspiration peak')
    assert raw['label'] == {'code': 'marker', 'text': 'inspiration peak',
                            'attributes': {
                                AMPLITUDE_LABEL_NAME_ATTRIBUTE: 'inspiration peak',
                                AMPLITUDE_LABEL_COLOR_ATTRIBUTE: MARKER_COLORS[0],
                            }}
    assert v.sources['ch0'][1].data['label'] == ['inspiration peak']
    assert v.sources['ch0'][1].data['color'] == [MARKER_COLORS[0]]
    assert v.sources['ch0'][1].data['marker'] == ['circle']


def test_plot_selected_generic_point_reassignment_preserves_other_state_and_reloads(tmp_path):
    v = viewer(tmp_path)
    first = v.service.add_amplitude_label('old name')
    second = v.service.add_amplitude_label('new name')
    point = v.service.add_catalog_marker('ch0', .1234, first['name'])
    v.refresh()
    before_raw = json.loads(v.sidecar.read_text())['annotations'][0]

    assert v.doc.get_model_by_name('assign_marker_label') is v.assign_marker_label
    assert v.doc.get_model_by_name('save_marker_label') is None
    v.sources['ch0'][1].selected.indices = [0]
    v.amplitude_label_select.value = second['name']
    v.run(v.reassign_selected_point_label)

    renamed = v.service.workspace.annotations[0]
    expected_attributes = {AMPLITUDE_LABEL_NAME_ATTRIBUTE: second['name'],
                           AMPLITUDE_LABEL_COLOR_ATTRIBUTE: second['color']}
    assert renamed == replace(point, label=replace(point.label, text=second['name'],
                                                    attributes=expected_attributes))
    raw = json.loads(v.sidecar.read_text())['annotations'][0]
    assert raw == {**before_raw, 'label': {'code': 'marker', 'text': second['name'],
                                           'attributes': expected_attributes}}
    assert v.sources['ch0'][1].data['label'] == ['new name']

    reopened = viewer(tmp_path)
    assert reopened.service.workspace.annotations == [renamed]
    assert reopened.sources['ch0'][1].data['label'] == ['new name']


def test_point_reassignment_rejects_missing_label_or_invalid_selection_without_writing(tmp_path):
    v = viewer(tmp_path)
    entry = v.service.add_amplitude_label('first')
    first = v.service.add_catalog_marker('ch0', .12, entry['name'])
    second = v.service.add_catalog_marker('ch0', .24, entry['name'])
    v.refresh()
    before_workspace, before_bytes = deepcopy(v.service.workspace), v.sidecar.read_bytes()
    source = v.sources['ch0'][1]

    source.selected.indices = [0]
    v.amplitude_label_select.value = ''
    v.run(v.reassign_selected_point_label)
    assert 'Choose a registered amplitude label' in v.status.text
    assert v.service.workspace == before_workspace and v.sidecar.read_bytes() == before_bytes

    source.selected.indices = []
    v.amplitude_label_select.value = entry['name']
    v.run(v.reassign_selected_point_label)
    assert 'Select exactly one point' in v.status.text
    assert v.service.workspace == before_workspace and v.sidecar.read_bytes() == before_bytes

    v.syncing_annotation_selection = True
    source.selected.indices = [0, 1]
    v.syncing_annotation_selection = False
    v.run(v.reassign_selected_point_label)
    assert 'Select exactly one point' in v.status.text
    assert v.service.workspace == before_workspace and v.sidecar.read_bytes() == before_bytes

    source.selected.indices = [99]
    v.run(v.reassign_selected_point_label)
    assert 'Select exactly one point' in v.status.text
    assert [a.id for a in v.service.workspace.annotations] == [first.id, second.id]
    assert v.sidecar.read_bytes() == before_bytes


def test_point_reassignment_rejects_model_reviewed_point_without_memory_or_disk_change(tmp_path):
    v = viewer(tmp_path)
    channel = v.recording.channel('ch0')
    index, seconds, value = channel.sample_at(.12)
    proposal = Proposal('proposal-point-name',
        Location('point', ('ch0',), Coordinate(seconds, index), amplitude=Amplitude(channel.units, value)),
        Label('suggested_peak', 'suggested name', {'color': MARKER_COLORS[0]}),
        'future-model', 'v1', .9, '2026-10-04T00:00:00+00:00')
    v.service.register_proposals([proposal])
    v.service.review_proposal(proposal.id, True)
    entry = v.service.add_amplitude_label('registered')
    v.refresh()
    before_workspace, before_bytes = deepcopy(v.service.workspace), v.sidecar.read_bytes()

    v.sources['ch0'][1].selected.indices = [0]
    v.amplitude_label_select.value = entry['name']
    v.run(v.reassign_selected_point_label)

    assert 'Only manual generic point annotations can be reassigned' in v.status.text
    assert v.service.workspace == before_workspace
    assert v.sidecar.read_bytes() == before_bytes


def test_legacy_maximum_minimum_points_still_render_and_move(tmp_path):
    v = viewer(tmp_path)
    maximum = v.service.add('maximum', 'ch0', .12, None, 'legacy maximum')
    minimum = v.service.add('minimum', 'ch0', .24, None, 'legacy minimum')
    reopened = viewer(tmp_path)
    source = reopened.sources['ch0'][1]
    assert source.data['label'] == ['legacy maximum', 'legacy minimum']
    assert source.data['color'] == list(MARKER_COLORS[:2])
    assert source.data['marker'] == ['triangle', 'inverted_triangle']

    changed = dict(source.data)
    changed['x'] = [.12, .2471]
    source.data = changed
    moved = reopened.service.workspace.annotations[1]
    assert moved.id == minimum.id
    assert moved.label == minimum.label
    assert moved.start == reopened.recording.channel('ch0').sample_at(.2471)[1]
    assert reopened.service.workspace.annotations[0] == maximum


def test_pan_and_partial_selection(tmp_path):
    v = viewer(tmp_path)
    v.x_range.start, v.x_range.end = 10, 12
    v.refresh()
    assert all(9.95 <= min(s[0].data['x']) <= 10 for s in v.sources.values())
    assert all(12 <= max(s[0].data['x']) <= 12.05 for s in v.sources.values())
    v.mode.value = 'interval'
    v.selection(SelectionGeometry(model=v.plots['ch0'], geometry={'type':'rect','x0':1.,'x1':2.}, final=False), 'ch0')
    assert not v.service.workspace.annotations


def test_plot_context_drives_channel_local_numeric_and_drag_creation(tmp_path):
    v = viewer(tmp_path)
    v.amplitude_label_name.value = 'peak'
    v.run(v.create_amplitude_label)
    v.mode.value = 'marker'
    v.start.value = .12
    v.run(v.add_numeric)
    assert v.service.workspace.annotations[-1].location.channel_ids == ('ch0',)

    v.mode.value = 'navigate'
    v.tap(Tap(model=v.plots['ch4'], x=.2, y=0), 'ch4')
    assert v.active_channel_id == 'ch4'
    v.mode.value = 'marker'
    v.start.value = .24
    v.run(v.add_numeric)
    assert v.service.workspace.annotations[-1].location.channel_ids == ('ch4',)

    v.interval_label_name.value = 'artifact'
    v.run(v.create_interval_label)
    v.mode.value = 'interval'
    v.selection(SelectionGeometry(model=v.plots['ch2'],
        geometry={'type': 'rect', 'x0': 2., 'x1': 1.}, final=True), 'ch2')
    assert v.active_channel_id == 'ch2'
    assert v.service.workspace.annotations[-1].location.channel_ids == ('ch2',)
    v.start.value, v.end.value = 3., 4.
    v.run(v.add_numeric)
    assert v.service.workspace.annotations[-1].location.channel_ids == ('ch2',)
    assert all(annotation.location.channel_ids
               for annotation in v.service.workspace.annotations)

    v.mode.value = 'navigate'
    v.sources['ch4'][1].selected.indices = [0]
    assert v.active_channel_id == 'ch4'


def test_time_scrollbar_tracks_and_changes_the_shared_visible_window(tmp_path):
    v = viewer(tmp_path)

    v.time_scrollbar.value = (4., 7.5)
    assert (v.x_range.start, v.x_range.end) == (4., 7.5)
    assert (v.view_start.value, v.view_width.value) == (4., 3.5)
    assert all(plot.x_range is v.x_range for plot in v.plots.values())

    v.x_range.start, v.x_range.end = 10., 14.
    assert v.time_scrollbar.value == (10., 14.)
    assert (v.view_start.value, v.view_width.value) == (10., 4.)

    v.view_start.value, v.view_width.value = 15., 2.
    v.run(v.go_window)
    assert (v.x_range.start, v.x_range.end) == (15., 17.)
    assert v.time_scrollbar.value == (15., 17.)


def test_time_scrollbar_and_shared_range_respect_recording_bounds(tmp_path):
    v = viewer(tmp_path)
    duration = v.recording.duration

    v.view_start.value, v.view_width.value = duration - 1., 8.
    v.run(v.go_window)
    assert (v.x_range.start, v.x_range.end) == (duration - 1., duration)
    assert v.time_scrollbar.value == (duration - 1., duration)
    assert (v.view_start.value, v.view_width.value) == (duration - 1., 1.)

    v.x_range.start, v.x_range.end = duration - 2., duration + 3.
    assert (v.x_range.start, v.x_range.end) == pytest.approx((duration - 5., duration))
    assert v.time_scrollbar.value == pytest.approx((duration - 5., duration))
    assert (v.view_start.value, v.view_width.value) == pytest.approx((duration - 5., 5.))


def test_new_document_automatically_renders_existing_sidecar(tmp_path):
    v = viewer(tmp_path)
    v.service.add('maximum','ch0',.24,None,'existing point')
    v.service.add('interval','ch1',1,2,'existing interval')
    reopened = viewer(tmp_path)
    assert reopened.sources['ch0'][1].data['label'] == ['existing point']
    assert reopened.sources['ch1'][2].data['label'] == ['existing interval']
    assert reopened.sources['ch1'][2].data['color'] == [LEGACY_INTERVAL_COLOR]
    assert reopened.interval_handle_sources['ch1']['start'].data['color'] == [LEGACY_INTERVAL_COLOR]


def test_amplitude_catalog_limit_is_independent_and_one_label_supports_many_points(tmp_path):
    v = viewer(tmp_path)
    entries = [v.service.add_amplitude_label(f'label {i}') for i in range(10)]
    points = [v.service.add_catalog_marker(f'ch{i % 2}', .02 * (i + 1), entries[0]['name'])
              for i in range(12)]
    v.service.add_interval_label('independent interval label')
    before_workspace = v.service.workspace
    before_bytes = v.sidecar.read_bytes()

    assert all(point.label.attributes['color'] == entries[0]['color'] for point in points)
    with pytest.raises(ValidationError, match='At most 10 amplitude labels'):
        v.service.add_amplitude_label('eleventh')

    assert v.service.workspace == before_workspace
    assert v.sidecar.read_bytes() == before_bytes
    reopened = viewer(tmp_path)
    reloaded_points = [a for a in reopened.service.workspace.annotations if a.location.type == 'point']
    assert len(reloaded_points) == 12
    assert all(a.label.attributes['color'] == entries[0]['color'] for a in reloaded_points)
    rendered = [color for channel in ('ch0', 'ch1')
                for color in reopened.sources[channel][1].data['color']]
    assert rendered == [entries[0]['color']] * 12


def test_deleting_point_does_not_change_catalog_or_reallocate_label_color(tmp_path):
    v = viewer(tmp_path)
    entry = v.service.add_amplitude_label('shared')
    first = v.service.add_catalog_marker('ch0', .12, entry['name'])
    second = v.service.add_catalog_marker('ch1', .12, entry['name'])

    v.service.delete([first.id])
    replacement = v.service.add_catalog_marker('ch2', .12, entry['name'])

    assert v.service.workspace.metadata[AMPLITUDE_LABEL_CATALOG_KEY] == [entry]
    assert replacement.label.attributes['color'] == entry['color']
    reopened = viewer(tmp_path)
    assert reopened.sources['ch1'][1].data['color'] == [entry['color']]
    assert reopened.sources['ch2'][1].data['color'] == [entry['color']]


def test_interval_catalog_controls_create_colored_linked_interval_and_reload(tmp_path):
    v = viewer(tmp_path)
    assert v.interval_label_select.options == [('', 'Choose a registered label')]
    assert v.doc.get_model_by_name('interval_label_name') is v.interval_label_name
    assert v.doc.get_model_by_name('interval_label_select') is v.interval_label_select
    assert v.doc.get_model_by_name('add_interval_label') is not None

    v.interval_label_name.value = 'Movement artifact'
    v.run(v.create_interval_label)
    entry = v.service.workspace.metadata[INTERVAL_LABEL_CATALOG_KEY][0]
    assert v.interval_label_select.options[-1] == (entry['name'], entry['name'])
    assert v.interval_label_select.value == entry['name']

    v.mode.value = 'interval'
    v.selection(SelectionGeometry(model=v.plots['ch2'],
        geometry={'type': 'rect', 'x0': 2., 'x1': 1.}, final=True), 'ch2')
    annotation = v.service.workspace.annotations[0]
    assert annotation.location.channel_ids == ('ch2',)
    assert annotation.label == Label('interval', entry['name'], {
        INTERVAL_LABEL_NAME_ATTRIBUTE: entry['name'],
        INTERVAL_LABEL_COLOR_ATTRIBUTE: entry['color'],
    })
    raw = json.loads(v.sidecar.read_text())
    assert raw['metadata'][INTERVAL_LABEL_CATALOG_KEY] == [entry]
    assert raw['annotations'][0]['label']['attributes'][INTERVAL_LABEL_COLOR_ATTRIBUTE] == entry['color']
    assert v.sources['ch2'][2].data['color'] == [entry['color']]
    assert all(not source[2].data['color'] for channel_id, source in v.sources.items()
               if channel_id != 'ch2')
    assert v.interval_handle_sources['ch2']['start'].data['color'] == [entry['color']]
    assert v.interval_handle_sources['ch2']['end'].data['color'] == [entry['color']]

    reopened = viewer(tmp_path)
    assert reopened.interval_label_select.options[-1] == (entry['name'], entry['name'])
    assert reopened.sources['ch2'][2].data['color'] == [entry['color']]
    assert reopened.interval_handle_sources['ch2']['start'].data['color'] == [entry['color']]
    assert reopened.interval_handle_sources['ch2']['end'].data['color'] == [entry['color']]


def test_interval_creation_requires_valid_menu_selection_without_mutation(tmp_path):
    v = viewer(tmp_path)
    v.mode.value = 'interval'
    v.start.value, v.end.value = 1., 2.
    before_workspace, before_bytes = deepcopy(v.service.workspace), v.sidecar.read_bytes()

    v.run(v.add_numeric)
    assert 'Choose a registered interval label' in v.status.text
    assert v.service.workspace == before_workspace
    assert v.sidecar.read_bytes() == before_bytes

    v.interval_label_name.value = 'Valid'
    v.run(v.create_interval_label)
    v.interval_label_select.value = 'not registered'
    before_workspace, before_bytes = deepcopy(v.service.workspace), v.sidecar.read_bytes()
    v.run(v.add_numeric)
    assert 'Choose a registered interval label' in v.status.text
    assert v.service.workspace == before_workspace
    assert v.sidecar.read_bytes() == before_bytes


def test_interval_catalog_control_rejects_blank_and_duplicate_without_mutation(tmp_path):
    v = viewer(tmp_path)
    for invalid in ('', '   '):
        before_workspace, before_bytes = deepcopy(v.service.workspace), v.sidecar.read_bytes()
        v.interval_label_name.value = invalid
        v.run(v.create_interval_label)
        assert v.service.workspace == before_workspace
        assert v.sidecar.read_bytes() == before_bytes

    v.interval_label_name.value = 'Artifact'
    v.run(v.create_interval_label)
    before_workspace, before_bytes = deepcopy(v.service.workspace), v.sidecar.read_bytes()
    v.interval_label_name.value = 'artifact'
    v.run(v.create_interval_label)
    assert 'unique' in v.status.text
    assert v.service.workspace == before_workspace
    assert v.sidecar.read_bytes() == before_bytes


def test_interval_creation_has_no_extra_controls_and_keeps_box_drag(tmp_path):
    v = viewer(tmp_path)
    v.interval_label_name.value = 'Two gestures'
    v.run(v.create_interval_label)

    assert v.doc.get_model_by_name('interval_creation_mode') is None
    assert v.doc.get_model_by_name('interval_creation_status') is None
    assert not hasattr(v, 'interval_creation_mode')
    assert not hasattr(v, 'interval_creation_status')
    assert v.pending_interval_start is None

    v.mode.value = 'interval'
    assert all(plot.toolbar.active_drag is v.sources[channel_id][3]
               for channel_id, plot in v.plots.items())


def test_click_then_ctrl_click_creates_local_catalog_interval_and_reloads(tmp_path):
    v = viewer(tmp_path)
    v.interval_label_name.value = 'Artifact'
    v.run(v.create_interval_label)
    entry = v.service.workspace.metadata[INTERVAL_LABEL_CATALOG_KEY][0]
    v.mode.value = 'interval'
    before = v.sidecar.read_bytes()

    v.tap(Tap(model=v.plots['ch0'], x=1., y=0, modifiers={}), 'ch0')
    assert v.service.workspace.annotations == []
    assert v.sidecar.read_bytes() == before
    assert v.pending_interval_start == {
        'start': 1., 'channel_id': 'ch0', 'label': entry['name']}
    assert 'Ctrl+click a later time on the same plot' in v.status.text

    v.tap(Tap(model=v.plots['ch0'], x=2., y=0, modifiers={'ctrl': True}), 'ch0')
    assert v.pending_interval_start is None
    annotation = v.service.workspace.annotations[0]
    assert annotation.location.channel_ids == ('ch0',)
    assert (annotation.start, annotation.end, annotation.label.text) == (1., 2., entry['name'])
    raw = json.loads(v.sidecar.read_text())['annotations'][0]
    assert raw['location']['channel_ids'] == ['ch0']
    assert raw['location']['start']['seconds'] == 1.
    assert raw['location']['end']['seconds'] == 2.
    assert raw['label']['attributes'][INTERVAL_LABEL_COLOR_ATTRIBUTE] == entry['color']

    reopened = viewer(tmp_path)
    assert reopened.service.workspace.annotations == [annotation]
    assert reopened.sources['ch0'][2].data['left'] == [1.]
    assert reopened.sources['ch0'][2].data['right'] == [2.]


def test_click_ctrl_interval_rejects_invalid_or_interrupted_gestures(tmp_path):
    v = viewer(tmp_path)
    v.mode.value = 'interval'
    before = v.sidecar.read_bytes()

    v.tap(Tap(model=v.plots['ch0'], x=1., y=0, modifiers={}), 'ch0')
    assert v.pending_interval_start is None
    assert 'registered interval label' in v.status.text

    v.interval_label_name.value = 'Artifact'
    v.run(v.create_interval_label)
    v.tap(Tap(model=v.plots['ch0'], x=1., y=0, modifiers={}), 'ch0')
    v.tap(Tap(model=v.plots['ch1'], x=2., y=0, modifiers={'ctrl': True}), 'ch1')
    assert v.pending_interval_start is None
    assert 'same plot' in v.status.text

    v.tap(Tap(model=v.plots['ch0'], x=2., y=0, modifiers={}), 'ch0')
    v.tap(Tap(model=v.plots['ch0'], x=1., y=0, modifiers={'ctrl': True}), 'ch0')
    assert v.pending_interval_start is None
    assert 'later than its start' in v.status.text

    v.tap(Tap(model=v.plots['ch0'], x=1., y=0, modifiers={}), 'ch0')
    v.interval_label_select.value = ''
    assert v.pending_interval_start is None
    assert 'label changed' in v.status.text

    v.interval_label_select.value = 'Artifact'
    v.tap(Tap(model=v.plots['ch0'], x=1., y=0, modifiers={}), 'ch0')
    v.mode.value = 'navigate'
    assert v.pending_interval_start is None
    assert 'action changed' in v.status.text
    assert v.service.workspace.annotations == []
    assert v.sidecar.read_bytes() != before  # Only the label catalog was saved.


def test_pending_click_interval_is_cleared_by_form_action_and_ctrl_without_start(tmp_path):
    v = viewer(tmp_path)
    v.interval_label_name.value = 'Artifact'
    v.run(v.create_interval_label)
    v.mode.value = 'interval'
    after_label = v.sidecar.read_bytes()

    v.tap(Tap(model=v.plots['ch0'], x=1., y=0, modifiers={}), 'ch0')
    v.run(v.service.reload)
    assert v.pending_interval_start is None

    v.tap(Tap(model=v.plots['ch0'], x=2., y=0, modifiers={'ctrl': True}), 'ch0')
    assert 'Click an interval start' in v.status.text
    assert v.service.workspace.annotations == []
    assert v.sidecar.read_bytes() == after_label
