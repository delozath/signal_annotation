# JSON contract · format 2

`schema_version` is a format version, never an edit counter. The sidecar stores only the
current state. It contains no history, audit log, snapshots, or complete signal data.

## Root

- `schema_version`: 2.
- `recording`: SHA-256 ID (or synthetic ID), name, byte size, synthetic origin, time
  base, and a channel list containing ID, name, units, sampling rate, and sample count.
- `annotations`: current confirmed annotations.
- `proposals`: current proposals and review state, separate from annotations.
- `relationships`: directed relationships between annotations and/or channels.
- `metadata`: user/protocol-defined recording metadata plus the reserved optional
  `amplitude_label_catalog`, `interval_label_catalog`, and `general_notes` described below.
- `extensions`: future namespaced properties, preferably prefixed by organization or
  protocol to avoid collisions.

All structural keys are required; empty lists and objects are valid. Optional geometric
fields appear as `null`. An initial valid empty file is included at
`examples/demo.empty.annotations.json`.

## Annotation

```json
{
  "id": "event-1",
  "location": {
    "type": "point",
    "channel_ids": ["ch0"],
    "start": {"seconds": 0.24, "sample_index": 120},
    "end": null,
    "amplitude": {"units": "mV", "value": 1.0, "minimum": null, "maximum": null}
  },
  "label": {"code": "maximum", "text": "Manual maximum", "attributes": {}},
  "provenance": {
    "origin": "manual", "author": "annotator01",
    "created_at": "2026-10-02T00:00:00+00:00", "proposal_id": null
  },
  "extensions": {}
}
```

This fragment illustrates the structure; amplitude 1.0 does not represent a demo
sample. For complete valid examples, see `examples/demo.annotations.json`.

## Locations

- `point`: exactly one channel; a time start and point amplitude; no end. If a
  `sample_index` is present, it must be a valid integer satisfying
  `seconds = index / fs`, and the amplitude must match that sample and its units. If the
  index is `null`, it is a free time/amplitude point; it must still fall within the
  channel and use its units. The current interface always creates sample-anchored points.
- `interval`: start and end with `start < end`; no amplitude. `channel_ids: []` means
  global; one or more channels constrain it to the common available interval. Bounds
  are continuous by default; an optional index requires one channel and time consistency.
  Dragging one endpoint in the current interface replaces only that coordinate with a
  continuous-seconds coordinate (`sample_index: null`) and persists it immediately.
- `region`: a time/amplitude box on exactly one channel. It has a start/end and
  minimum/maximum amplitude, with minimum < maximum. The point value is `null`. The box
  amplitude need not match observed values, but its units must.
- `recording`: the complete recording. Empty channel list; `null` start, end, and
  amplitude.

Locations do not encode meaning: a region may represent an artifact, review, or any
other code; a point may be a maximum, minimum, or another event. Codes and texts are
strings, not closed enums. New catalog-created amplitude markers persist their selected
name, fixed palette color, and semantic catalog link; older points without those
attributes remain valid.
`label.attributes` accepts semantic JSON attributes, such as
a project-defined quality scale.

## Amplitude-label catalog

The optional `metadata.amplitude_label_catalog` is independent of the interval catalog
and has the same `{name, color}` entry shape. It accepts at most ten entries. Names must
be nonblank and unique (case-insensitively), and colors must be distinct members of the
predefined amplitude palette. The limit applies to catalog entries, not point instances:
any number of markers may reuse the same entry.

A new marker created by the UI requires a selected entry. Its `label.text` contains the
selected name and `label.attributes` contains both `color` and
`amplitude_label_catalog_name`. Those values must continue to match the catalog. A
selected manual generic marker may be reassigned only to another registered entry;
geometry, identity, provenance, extensions, and ordering are preserved. Sidecars that
predate the catalog, including unlinked generic and maximum/minimum points, remain valid
and render with deterministic fallback colors.

## Interval-label catalog

The optional `metadata.interval_label_catalog` is independent of the amplitude-label catalog and has
this current-state shape:

```json
[
  {"name": "Movement artifact", "color": "#6a3d9a"},
  {"name": "Sleep", "color": "#ff7f00"}
]
```

It accepts at most ten entries. Names must be nonblank and unique (case-insensitively),
and colors must be distinct members of the predefined interval palette. Other metadata
is preserved when an entry is added. The limit applies only to catalog entries: many
interval annotations may reuse the same entry.

A new interval created by the UI requires a selected entry. Its `label.text` contains
the selected name and `label.attributes` contains both `color` and
`interval_label_catalog_name`. Those three values must continue to match the catalog,
which is the semantic link between the interval instance and its type. Invalid catalog
state or a missing selection is rejected without replacing the current JSON. Sidecars
that predate this catalog remain valid; their intervals render with the deterministic
legacy fallback color.

## General recording notes

The optional `metadata.general_notes` is a recording-level JSON string containing free
text observations. It is not attached to any annotation and has no application-imposed
length limit. The UI saves the field immediately. Clearing it persists the explicit
empty-string state, `"general_notes": ""`, rather than removing the reserved key. A
non-string reserved value is invalid and is rejected without replacing the sidecar.

## Relationships

Each relationship has an ID, `source`, `target`, `label`, and `extensions`. An endpoint
is `{ "type": "annotation", "id": "event-1" }` or
`{ "type": "channel", "id": "ch3" }`. Annotation→annotation,
annotation→channel, channel→annotation, and channel→channel are supported. The label
determines their meaning. Relationships are never inferred automatically. Missing and
duplicate IDs are rejected. The application blocks deletion of referenced annotations
to avoid leaving orphaned references.

## Provenance and proposals

`origin` is `manual` or `model_reviewed`. The latter requires an accepted proposal with
the same location/label and reviewer, referenced by `proposal_id`. A proposal adds the
model, version, optional confidence [0,1], date, and `pending/accepted/rejected` state,
with reviewer/date where applicable. There is no review history: only the current
proposal and decision. `created_at` is creation metadata, not an action log. Manual
edits preserve the ID and creation provenance without storing previous values.

## Validation and evolution

`schema/annotations.schema.json` uses JSON Schema 2020-12. It checks shape and structural
combinations. The code adds checks that JSON Schema cannot express by itself: equality
with the open ACQ file, references, channel bounds, index/time/amplitude consistency,
and proposal/annotation correspondence.
The code additionally enforces amplitude- and interval-catalog name/color uniqueness
and each catalog-created annotation's semantic link to its catalog entry.

Duplicate JSON keys, non-finite numbers, unexpected fields outside extensible areas,
and unknown format versions are rejected. Valid extensions are preserved when loading,
editing another annotation, and saving. Adding a new geometry type will require explicit
support and probably another format version, without silently reinterpreting existing
documents.
