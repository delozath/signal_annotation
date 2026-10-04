"""Regenerate the documented JSON Schema (no network access)."""
import json
from pathlib import Path


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def ref(name): return {"$ref": "#/$defs/" + name}

def nullable(schema): return {"anyOf": [schema, {"type": "null"}]}

string = {"type":"string", "minLength":1}
json_object = {"type":"object"}
number = {"type":"number"}
coordinate = obj({"seconds":{"type":"number","minimum":0}, "sample_index":nullable({"type":"integer","minimum":0})})
amplitude = obj({"units":{"type":"string"}, "value":nullable(number), "minimum":nullable(number), "maximum":nullable(number)})
location = obj({"type":{"enum":["point","interval","region","recording"]},
                "channel_ids":{"type":"array","items":string,"uniqueItems":True},
                "start":nullable(ref("coordinate")), "end":nullable(ref("coordinate")), "amplitude":nullable(ref("amplitude"))})
def condition(kind, properties):
    return {"if": {"properties": {"type": {"const": kind}}}, "then": {"properties": properties}}

location["allOf"] = [
    condition("point", {"channel_ids":{"minItems":1,"maxItems":1}, "start":ref("coordinate"), "end":{"type":"null"},
        "amplitude":{"allOf":[ref("amplitude"), {"properties":{"value":number,"minimum":{"type":"null"},"maximum":{"type":"null"}}}]}}),
    condition("interval", {"start":ref("coordinate"), "end":ref("coordinate"), "amplitude":{"type":"null"}}),
    condition("region", {"channel_ids":{"minItems":1,"maxItems":1}, "start":ref("coordinate"), "end":ref("coordinate"),
        "amplitude":{"allOf":[ref("amplitude"), {"properties":{"value":{"type":"null"},"minimum":number,"maximum":number}}]}}),
    condition("recording", {"channel_ids":{"maxItems":0},"start":{"type":"null"},"end":{"type":"null"},"amplitude":{"type":"null"}}),
]
label = obj({"code":{"type":"string","minLength":1,"maxLength":80}, "text":{"type":"string","minLength":1,"maxLength":200}, "attributes":json_object})
provenance = obj({"origin":{"enum":["manual","model_reviewed"]}, "author":{"type":"string","minLength":1,"maxLength":80},
                  "created_at":{"type":"string","format":"date-time"}, "proposal_id":nullable(string)})
annotation = obj({"id":string,"location":ref("location"),"label":ref("label"),"provenance":ref("provenance"),"extensions":json_object})
proposal = obj({"id":string,"location":ref("location"),"label":ref("label"),"model_name":string,"model_version":string,
                "confidence":nullable({"type":"number","minimum":0,"maximum":1}),"created_at":{"type":"string","format":"date-time"},
                "status":{"enum":["pending","accepted","rejected"]},"reviewed_by":nullable(string),
                "reviewed_at":nullable({"type":"string","format":"date-time"}),"extensions":json_object})
reference = obj({"type":{"enum":["annotation","channel"]},"id":string})
relationship = obj({"id":string,"source":ref("reference"),"target":ref("reference"),"label":ref("label"),"extensions":json_object})
channel = obj({"id":string,"name":{"type":"string"},"units":{"type":"string"},"sample_rate_hz":{"type":"number","exclusiveMinimum":0},"sample_count":{"type":"integer","minimum":1}})
recording = obj({"id":string,"file_name":string,"file_size_bytes":nullable({"type":"integer","minimum":1}),"synthetic":{"type":"boolean"},
                 "time_basis":{"const":"relative_seconds_from_sample_zero"},"channels":{"type":"array","minItems":1,"maxItems":8,"items":ref("channel")}})
schema = obj({"schema_version":{"const":2},"recording":ref("recording"),
              "annotations":{"type":"array","items":ref("annotation")},"proposals":{"type":"array","items":ref("proposal")},
              "relationships":{"type":"array","items":ref("relationship")},"metadata":json_object,"extensions":json_object})
schema.update({"$schema":"https://json-schema.org/draft/2020-12/schema", "title":"ACQ Annotation Sidecar · format 2",
               "description":"Current state only. Cross-reference, ACQ identity, sample/time/amplitude and bounds checks are additionally enforced by Python.",
               "$defs":dict(coordinate=coordinate,amplitude=amplitude,location=location,label=label,provenance=provenance,
                             annotation=annotation,proposal=proposal,reference=reference,relationship=relationship,channel=channel,recording=recording)})
if __name__ == '__main__':
    path=Path(__file__).resolve().parents[1]/'schema'/'annotations.schema.json'
    path.write_text(json.dumps(schema,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(path)
