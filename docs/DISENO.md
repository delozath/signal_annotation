# Design · release 0.2.0

## Minimal hexagonal architecture

```text
CLI (composition)
  ├─ bioread → Recording + Channel
  ├─ JSON sidecar ↔ WorkspaceRepository port
  └─ Bokeh → AnnotationService → domain/validation
                                      ↑
                          ProposalProvider (future extension)
```

- `domain.py`: data, locations, meaning, and provenance; standard library only.
- `validation.py`: geometry, sample, identity, and reference invariants.
- `application.py`: add/edit/delete/reload operations and proposal registration/review.
- `ports.py`: reader, persistence, and future provider contracts.
- `adapters/readers.py`: bioread and synthetic demo, with read-only NumPy arrays.
- `adapters/storage.py`: the only persistent adapter, explicit JSON + CSV.
- `adapters/display.py`: data reduction for drawing only.
- `adapters/bokeh_ui.py`: widgets, events, plots, and error messages.
- `cli.py`: input selection, adjacent path, and a loopback-only server.

There is no database, ORM, migrator, history, audit log, snapshots, or undo stack. The
`examples` folder illustrates the format; it is not an alternative persistence layer.

## Opening and saving

1. Read the local ACQ file and validate its size/channels. A failure here does not
   create the JSON file.
2. Derive `file.annotations.json` next to the real ACQ path.
3. If it does not exist, create a complete empty state with file and channel metadata.
4. If it exists, validate its structure, format, ACQ identity, geometry, and references.
5. Build the Bokeh interface with the annotations already loaded.
6. For every create/edit/delete operation, validate the candidate state, save it, and
   only then update the visible state. The candidate is transient, not history.

Saving uses a temporary file in the same folder, `flush`, file `fsync`, and
`os.replace`. This prevents partial JSON in normal interruption/write-failure cases.
Durability during a total power failure also depends on the operating system and
storage; no universal guarantee is made for network folders.

A transient lock folder serializes cooperating writers. A fingerprint of the content
read is retained only in RAM and compared before replacement. There is no JSON revision
counter. If the file changed, became corrupt, or disappeared, the save is rejected. It
is not silently overwritten or recreated.

Each session receives its own repository instance and fingerprint; that instance must
not be shared across services/sessions. An external editor that ignores the lock can
write during the window between checking and replacement, so use only one writing tool
at a time. Failure to clean up a lock after replacement produces a warning without
incorrectly claiming that an already committed write failed.

## Model and extensibility

Location is independent of meaning. `point`, `interval`, `region`, and `recording` are
geometries; `maximum`, `minimum`, `artifact`, `quality`, and so on are semantic codes.
Arbitrary metadata has explicit locations and is not removed when visible annotations
are edited. The contract is described in `ESQUEMA.md` and the JSON Schema.

Channel IDs are based on their indices, not names that may repeat. Recording identity
uses SHA-256 over the same bytes supplied to bioread. Name, size, sampling rates,
samples, units, and IDs are also compared when the sidecar is loaded. If an ACQ file is
renamed, the JSON name metadata must be updated explicitly along with the sidecar name,
while preserving the content hash; the JSON is not automatically reassigned to another
input.

A pending proposal is not a confirmed annotation. Accepting it creates a
`model_reviewed` annotation linked to the proposal, never a `manual` one. Rejecting it
changes only its current state. Relationships may reference only existing annotations
or channels. Deleting a referenced annotation is rejected, with no silent cascades.

## Time and representation

Relative time is `index / channel_sampling_rate`. Each channel retains its original
rate and samples. The `time_index` in bioread 3.1.0 uses an inclusive endpoint that
introduces slight stretching, so it is not used for annotation coordinates.

UI points snap to the nearest sample, with ties resolved toward the higher index. The
index, seconds, amplitude, and units are checked against the signal. A future free point
may omit the index. Intervals are continuous; if they include an index, it must be
consistent with the time and reference a single channel. A maximum/minimum label is not
required to be a mathematical extremum: that decision is manual.

All plots share one horizontal `Range1d`. Vertical axes are independent. For large
windows, a minimum/maximum envelope of up to approximately 4,000 vertices per channel
is shown, including gaps; for small windows, all samples are shown. Only the rendering
changes, never the data used for annotation. Acquisition pauses and native markers are
not reconstructed in this release.

## Security and limits

The server binds to `127.0.0.1`, with exact local WebSocket origins and no wildcards.
There are no CDN resources or calls to remote models/services. ACQ files are read-only;
JSON and CSV are local files that the user must protect. There is no authentication,
disk encryption, automatic backup, or clinical validation. The 20 MiB limit is checked
before reading and the 12-million-sample limit after decoding.

## Graphical scope

The UI renders loaded points and intervals. It creates channel-local manual points and
intervals; existing global intervals remain rendered,
resizable, reloadable, and preserved. Regions, recording
annotations, metadata, and relationships are validated and preserved, but their
graphical editors remain pending. CSV export includes all current annotations. The
schema supports proposals; the UI has no review inbox or detector.
