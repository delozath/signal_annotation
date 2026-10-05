import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from signal_annotation.adapters.readers import demo_recording
from signal_annotation.adapters.storage import from_dict, to_dict
from signal_annotation.validation import (AMPLITUDE_LABEL_CATALOG_KEY, AMPLITUDE_LABEL_COLORS,
                                          GENERAL_NOTES_KEY, INTERVAL_LABEL_COLORS)
ROOT = Path(__file__).resolve().parents[1]


def test_schema_and_examples():
    schema=json.loads((ROOT/'schema/annotations.schema.json').read_text())
    Draft202012Validator.check_schema(schema)
    validator=Draft202012Validator(schema,format_checker=FormatChecker())
    for path in (ROOT/'examples').glob('*.json'):
        raw=json.loads(path.read_text())
        validator.validate(raw)
        workspace=from_dict(raw,demo_recording())
        assert to_dict(workspace,demo_recording()) == raw


def test_invalid_region_schema():
    schema=json.loads((ROOT/'schema/annotations.schema.json').read_text())
    raw=json.loads((ROOT/'examples/demo.annotations.json').read_text())
    raw['annotations'][2]['location']['amplitude']['minimum']=None
    assert list(Draft202012Validator(schema).iter_errors(raw))


def test_interval_label_catalog_schema_accepts_ten_predefined_entries():
    schema=json.loads((ROOT/'schema/annotations.schema.json').read_text())
    raw=json.loads((ROOT/'examples/demo.annotations.json').read_text())
    raw['metadata']['interval_label_catalog'] = [
        {'name': f'Interval {index}', 'color': color}
        for index, color in enumerate(INTERVAL_LABEL_COLORS)
    ]
    Draft202012Validator(schema).validate(raw)
    workspace=from_dict(raw,demo_recording())
    assert to_dict(workspace,demo_recording()) == raw


def test_amplitude_label_catalog_schema_accepts_ten_predefined_entries():
    schema=json.loads((ROOT/'schema/annotations.schema.json').read_text())
    raw=json.loads((ROOT/'examples/demo.annotations.json').read_text())
    raw['metadata'][AMPLITUDE_LABEL_CATALOG_KEY] = [
        {'name': f'Amplitude {index}', 'color': color}
        for index, color in enumerate(AMPLITUDE_LABEL_COLORS)
    ]
    Draft202012Validator(schema).validate(raw)
    workspace=from_dict(raw,demo_recording())
    assert to_dict(workspace,demo_recording()) == raw


@pytest.mark.parametrize('catalog', [
    [{'name': '', 'color': INTERVAL_LABEL_COLORS[0]}],
    [{'name': ' padded', 'color': INTERVAL_LABEL_COLORS[0]}],
    [{'name': 'Unknown color', 'color': '#000000'}],
    [{'name': f'Interval {index}', 'color': INTERVAL_LABEL_COLORS[index % 10]}
     for index in range(11)],
])
def test_interval_label_catalog_schema_rejects_invalid_boundaries(catalog):
    schema=json.loads((ROOT/'schema/annotations.schema.json').read_text())
    raw=json.loads((ROOT/'examples/demo.annotations.json').read_text())
    raw['metadata']['interval_label_catalog'] = catalog
    assert list(Draft202012Validator(schema).iter_errors(raw))


@pytest.mark.parametrize('catalog', [
    [{'name': '', 'color': AMPLITUDE_LABEL_COLORS[0]}],
    [{'name': ' padded', 'color': AMPLITUDE_LABEL_COLORS[0]}],
    [{'name': 'Unknown color', 'color': '#000000'}],
    [{'name': f'Amplitude {index}', 'color': AMPLITUDE_LABEL_COLORS[index % 10]}
     for index in range(11)],
])
def test_amplitude_label_catalog_schema_rejects_invalid_boundaries(catalog):
    schema=json.loads((ROOT/'schema/annotations.schema.json').read_text())
    raw=json.loads((ROOT/'examples/demo.annotations.json').read_text())
    raw['metadata'][AMPLITUDE_LABEL_CATALOG_KEY] = catalog
    assert list(Draft202012Validator(schema).iter_errors(raw))


@pytest.mark.parametrize('notes', ['', 'Observation one\nObservation two\n  preserved spacing'])
def test_general_notes_schema_and_round_trip(notes):
    schema=json.loads((ROOT/'schema/annotations.schema.json').read_text())
    raw=json.loads((ROOT/'examples/demo.annotations.json').read_text())
    raw['metadata'][GENERAL_NOTES_KEY] = notes
    Draft202012Validator(schema).validate(raw)
    workspace=from_dict(raw,demo_recording())
    assert to_dict(workspace,demo_recording()) == raw


@pytest.mark.parametrize('notes', [None, [], {'text': 'not free text'}, 7])
def test_general_notes_schema_rejects_non_strings(notes):
    schema=json.loads((ROOT/'schema/annotations.schema.json').read_text())
    raw=json.loads((ROOT/'examples/demo.annotations.json').read_text())
    raw['metadata'][GENERAL_NOTES_KEY] = notes
    assert list(Draft202012Validator(schema).iter_errors(raw))
