"""Generate patient-free schema examples and current annotation CSV."""
from dataclasses import replace
import json
from pathlib import Path
from signal_annotation.adapters.readers import demo_recording
from signal_annotation.adapters.storage import to_dict, export_annotations_csv
from signal_annotation.domain import Annotation, Amplitude, Coordinate, Label, Location, Provenance, Reference, Relationship, Workspace

root = Path(__file__).resolve().parents[1] / 'examples'
recording = demo_recording()
workspace = Workspace(recording.id)
(root/'demo.empty.annotations.json').write_text(json.dumps(to_dict(workspace,recording),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
prov = Provenance('manual','synthetic-example','2026-10-02T00:00:00+00:00')
ch = recording.channels[0]
i,t,value = ch.sample_at(.24)
workspace.annotations = [
    Annotation('event-1',Location('point',('ch0',),Coordinate(t,i),amplitude=Amplitude(ch.units,value)),Label('maximum','Example manual maximum'),prov),
    Annotation('event-2',Location('interval',(),Coordinate(1),Coordinate(2)),Label('artifact','Example global interval'),prov),
    Annotation('region-1',Location('region',('ch0',),Coordinate(3),Coordinate(4),Amplitude('mV',minimum=-.2,maximum=.7)),Label('review_area','Future review region'),prov,{'example':True}),
    Annotation('record-1',Location('recording'),Label('quality','Synthetic recording',{'status':'illustrative'}),prov),
]
workspace.relationships = [Relationship('relation-1',Reference('annotation','event-1'),Reference('channel','ch3'),Label('associated_channel','Example relationship, no automatic analysis'))]
workspace.metadata = {'protocol':'synthetic demo', 'contains_patient_data':False}
(root/'demo.annotations.json').write_text(json.dumps(to_dict(workspace,recording),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
export_annotations_csv(workspace,recording,root/'demo.annotations.csv')
print('Generated synthetic examples')
