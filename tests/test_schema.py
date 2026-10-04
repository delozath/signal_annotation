import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from signal_annotation.adapters.readers import demo_recording
from signal_annotation.adapters.storage import from_dict, to_dict
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
