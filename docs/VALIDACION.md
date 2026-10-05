# Validation · JSON release 0.2.0

October 2, 2026. Linux, Python 3.12.14, Bokeh 3.10.0, bioread 3.1.0. Complete
dependency versions are listed in `requirements-tested.txt`.

## Run and passed

- **65 automated tests passed**, including 54 independent-review tests.
- First use: a valid empty JSON file with metadata is created after a successful open;
  failure to open an ACQ file does not create JSON.
- Automatic loading of existing JSON and rendering of points/intervals.
- Create, edit, delete, and reload with current-state persistence; no
  history/audit/revision fields or undo stack.
- Protection against corruption, duplicate JSON keys, incompatible formats, ACQ
  identity mismatches, and inconsistent channel metadata; original bytes preserved.
- Heterogeneous sampling, anchored points, free points, intervals, and regions.
- Labels/extensions, recording annotations, relationships, and reference validation.
- Separate proposals and consistent provenance on acceptance/rejection.
- Stale saves, external changes/deletion, and residual locking.
- Injected temporary-file, fsync, and replacement failures; previous file and state
  preserved. Cleanup failure after a commit: consistent saved state and specific warning.
- JSON Schema 2020-12 and empty/enriched examples validated, plus semantic checks.
- Current-state CSV, visualization data sources, and edit/selection callbacks.
- Bokeh document with no validation errors/warnings; one horizontal range shared across
  six plots.
- Server smoke test: local HTTP, local JavaScript, and a real WebSocket with six channels.
- Python compilation and dependency consistency.

Previous tests that depended on the replaced persistence mechanism were replaced with
tests of the current contract. The number of tests does not imply clinical coverage.

## Pending / not run

- **A real user ACQ file:** none was provided. Adapter tests use a synthetic mocked API
  and deliberately invalid files; they do not verify a specific real AcqKnowledge
  version.
- **End-to-end visual browser testing:** the browser environment blocked loopback access
  in the previous delivery. That restriction was not bypassed. Server and callback
  checks do not replace visual BokehJS click/drag/render testing.
- Windows/macOS installation, graphically editable regions, and graphical editing of
  metadata/relationships; these are not presented as implemented.
- Real maximum-size files, discontinuous acquisitions/native markers, exhaustive
  security, complete accessibility, and any clinical validation.

## Short test on the target machine

1. Run the demo and confirm that only `demo.annotations.json` is created.
2. Zoom and drag: all time axes should remain synchronized.
3. Create a point and an interval; verify their JSON and the immediate update.
4. Move a selected point in **Navigate**, resize an interval endpoint, and delete a
   selected point with **Delete**; verify each accepted change immediately in the JSON.
5. Close and reopen: the remaining annotations should appear automatically.
6. Export CSV twice: it should replace the same CSV without additional JSON copies.
7. Test the enriched example: regions/recording annotations and
   relationships/metadata are preserved when another annotation changes.
8. Only then compare an authorized ACQ file against AcqKnowledge before using it for
   research annotation.
