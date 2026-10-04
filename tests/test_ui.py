import json
from bokeh.document import Document
from bokeh.events import SelectionGeometry, Tap
from bokeh.core.validation import check_integrity
from signal_annotation.application import AnnotationService
from signal_annotation.adapters.bokeh_ui import build_document
from signal_annotation.adapters.readers import demo_recording
from signal_annotation.adapters.storage import JsonSidecarRepository


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
    assert v.doc.get_model_by_name('undo') is None
    assert v.doc.to_json() is not None


def test_point_interval_edit_delete_reload_csv(tmp_path):
    v = viewer(tmp_path)
    assert v.sidecar.is_file()
    assert not json.loads(v.sidecar.read_text())['annotations']
    v.mode.value = 'maximum'
    v.tap(Tap(model=v.plots['ch0'], x=.2411, y=1), 'ch0')
    a = v.service.workspace.annotations[0]
    assert a.start == .242 and a.location.start.sample_index == 121
    assert 'JSON saved' in v.status.text
    v.mode.value = 'interval'
    v.channel_select.value = '__all__'
    v.selection(SelectionGeometry(model=v.plots['ch1'], geometry={'type':'rect','x0':3.,'x1':1.}, final=True), 'ch1')
    assert len(v.service.workspace.annotations) == 2
    for sources in v.sources.values(): assert sources[2].data['left'] == [1.]
    v.table_source.selected.indices = [0]
    v.load_selected()
    v.label.value = 'corrected'
    v.start.value = .5
    v.run(v.edit_selected)
    assert v.service.workspace.annotations[0].id == a.id
    assert v.service.workspace.annotations[0].label.text == 'corrected'
    v.table_source.selected.indices = [0]
    v.run(v.delete_selected)
    assert len(v.service.workspace.annotations) == 1
    v.run(v.service.reload)
    assert len(v.service.workspace.annotations) == 1
    v.export()
    assert v.sidecar.with_suffix('.csv').is_file()
    assert 'CSV actual' in v.status.text
    raw = json.loads(v.sidecar.read_text())
    assert 'revision' not in raw and 'audit' not in raw and 'history' not in raw


def test_invalid_action_survives_and_does_not_write(tmp_path):
    v = viewer(tmp_path)
    initial = v.sidecar.read_bytes()
    v.run(v.add_numeric)
    assert not v.service.workspace.annotations and 'could not be completed' in v.status.text
    v.mode.value = 'minimum'
    v.start.value = -1
    v.run(v.add_numeric)
    assert v.sidecar.read_bytes() == initial
    v.start.value = .1234
    v.run(v.add_numeric)
    assert len(v.service.workspace.annotations) == 1


def test_pan_and_partial_selection(tmp_path):
    v = viewer(tmp_path)
    v.x_range.start, v.x_range.end = 10, 12
    v.refresh()
    assert all(9.95 <= min(s[0].data['x']) <= 10 for s in v.sources.values())
    assert all(12 <= max(s[0].data['x']) <= 12.05 for s in v.sources.values())
    v.mode.value = 'interval'
    v.selection(SelectionGeometry(model=v.plots['ch0'], geometry={'type':'rect','x0':1.,'x1':2.}, final=False), 'ch0')
    assert not v.service.workspace.annotations


def test_new_document_automatically_renders_existing_sidecar(tmp_path):
    v = viewer(tmp_path)
    v.service.add('maximum','ch0',.24,None,'existing point')
    v.service.add('interval','ch1',1,2,'existing interval')
    reopened = viewer(tmp_path)
    assert reopened.sources['ch0'][1].data['label'] == ['existing point']
    assert reopened.sources['ch1'][2].data['label'] == ['existing interval']
