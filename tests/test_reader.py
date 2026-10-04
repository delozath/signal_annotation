from types import SimpleNamespace
import hashlib
import numpy as np
import pytest
import bioread
from signal_annotation.adapters.readers import BioreadReader, MAX_FILE_BYTES, demo_recording
from signal_annotation.domain import ValidationError


def test_bioread_adapter_with_synthetic_api_stub(tmp_path, monkeypatch):
    path=tmp_path/'input.acq'; path.write_bytes(b'FAKE ACQ FOR ADAPTER TEST ONLY')
    # Deliberately differing rates and duplicate names; no real format fixture.
    chs=[SimpleNamespace(name='ECG',units='mV',samples_per_second=100.,data=np.arange(100.)),
         SimpleNamespace(name='ECG',units='mV',samples_per_second=25.,data=np.arange(25.))]
    monkeypatch.setattr(bioread, 'read', lambda stream: SimpleNamespace(channels=chs))
    r=BioreadReader().read(path)
    assert r.id==hashlib.sha256(path.read_bytes()).hexdigest()
    assert r.channels[0].id != r.channels[1].id
    assert r.channels[1].sample_at(.52)[:2] == (13,.52)
    assert not r.channels[0].values.flags.writeable


def test_bad_input_and_decode_error(tmp_path, monkeypatch):
    with pytest.raises(ValidationError): BioreadReader().read(tmp_path/'missing.acq')
    empty=tmp_path/'empty.acq'; empty.touch()
    with pytest.raises(ValidationError): BioreadReader().read(empty)
    large=tmp_path/'large.acq'
    with large.open('wb') as f: f.truncate(MAX_FILE_BYTES+1)
    with pytest.raises(ValidationError): BioreadReader().read(large)
    bad=tmp_path/'bad.acq';bad.write_bytes(b'not an ACQ')
    with pytest.raises(ValidationError,match='bioread'): BioreadReader().read(bad)


def test_demo_is_repeatable_and_not_patient_data():
    a,b=demo_recording(),demo_recording()
    assert a.synthetic and len(a.channels)==6
    for x,y in zip(a.channels,b.channels): np.testing.assert_array_equal(x.values,y.values)


def test_failed_acq_open_does_not_create_json(tmp_path):
    import subprocess, sys
    source = tmp_path/'broken.acq'
    source.write_bytes(b'invalid acquisition')
    result=subprocess.run([sys.executable,'-m','signal_annotation','--file',str(source),'--no-browser'],capture_output=True,text=True)
    assert result.returncode != 0
    assert not source.with_suffix('.annotations.json').exists()
