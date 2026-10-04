# Signal Annotation · Local ACQ Viewer and Annotator · 0.2.0

Python + bioread + Bokeh. Opens an ACQ file, displays up to eight synchronized channels,
and supports manual annotation of maxima, minima, and intervals. This is a research
prototype with no clinical validation or automatic detector. The demo contains no
patient data.

## Persistence: one JSON file next to each ACQ file

```text
recordings/
  recording.acq
  recording.annotations.json
  another.acq
  another.annotations.json
```

After an ACQ file is opened successfully, a valid JSON file is created automatically if
none exists, including file/channel metadata and empty collections. If it already
exists, it is validated, loaded, and displayed automatically. Every create, edit, or
delete operation replaces the current JSON state. **There is no database, history,
audit log, annotation versioning, or Undo.** `schema_version: 2` identifies the format
only; it does not change when annotations are made.

The ACQ file is never modified. A corrupt or incompatible JSON file, or one associated
with another ACQ file, is rejected; it is not reset or overwritten. The folder must be
writable. The real file is resolved before the sidecar name is derived, so a symlink to
an ACQ file uses the JSON file next to the symlink target. Symbolic-link sidecars are
rejected.

## Installation

Python 3.12 and a modern browser are recommended. Tested on Linux with Python 3.12.14;
the Windows and macOS instructions have not been tested. The code supports Python 3.11
or newer. Unzip the archive and open a terminal in `signal_annotation`.

Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m signal_annotation --demo --demo-dir .\demo --author investigador01
```

macOS/Linux:

```bash
python3 -m venv ~/signal_annotation
source ~/signal_annotation/bin/activate

cd /path/to/signal_annotation
python -m pip install -e .
python -m signal_annotation --demo --demo-dir ./demo --author investigador01
```

Open `http://127.0.0.1:5006/`. Keep the terminal open; `Ctrl+C` stops the server.
The demo creates `demo/demo.annotations.json`. To open a real ACQ file:

```bash
python -m signal_annotation --file "/path/to/record.acq" --author investigador01
```

On Windows, use `.\.venv\Scripts\python.exe`. Quote paths containing spaces. File
selection is through the CLI; to change files, stop the app and run it again. There is
no option to save the real sidecar in another folder: it stays next to the ACQ file.

Options:

```text
--file FILE.acq    Local file, 1-8 channels, maximum 20 MiB
--demo             Six synthetic 30-second channels; excludes --file
--demo-dir DIR     Demo JSON location; defaults to the current directory
--author ALIAS     Author; defaults to local. A pseudonym is recommended
--port 5010        Alternative port if 5006 is in use
--no-browser       Do not open the browser automatically
```

Installation requires PyPI. Subsequent execution uses no external services.
`requirements-tested.txt` records the exact versions tested on Linux/Python 3.12; it
does not include Python or dependencies downloaded in the ZIP archive.

## Annotating and editing

1. Choose **Navigate**. Use the mouse wheel to zoom horizontally; drag to pan
   all channels together. You can also enter the window start and width.
2. Zoom in on the beat; choose **Mark manual maximum** or **Mark manual minimum**, enter
   a label, and click the channel.
   The nearest original sample is stored. Classifying it as a maximum or minimum is
   your decision, not an automatic calculation.
3. Alternatively, enter the channel and time and click **Add with these times**.
4. For intervals, select **Mark interval** and drag horizontally, or use numeric
   start/end values. **All: global interval** covers every channel.
5. To edit, select a row, click **Load selected row into form**, change the fields, and
   click **Save selected row edit**. The ID is preserved.
6. To delete, select the checkboxes and click **Delete selected rows**. The change is
   saved immediately and there is no Undo. An annotation
   referenced by a relationship cannot be deleted until that relationship is explicitly
   removed.

When you reopen the same ACQ file, its existing points and intervals are rendered. Use
one tab per ACQ file. If another tab or program modifies the JSON, the next stale save
is rejected; click **Reload saved state** and repeat your change.

## Extensible schema and interface scope

Each annotation separates `location` (geometry), `label` (meaning), `provenance`
(origin/author), and `extensions`. The root object adds metadata and relationships.

- Time/amplitude point: validated and rendered. The UI creates maxima/minima with a
  sample index. The schema supports free points with `sample_index: null`.
- Time interval: validated and rendered. The UI creates/edits one-channel or global
  intervals. The schema also supports intervals over a subset of channels.
- Rectangular time/amplitude region: validated and preserved; listed in the table, but
  without a dedicated editor or graphical shading yet.
- Whole-recording annotation: validated/preserved and visible in the table, but without
  a dedicated graphical editor.
- Recording metadata and relationships between annotations/channels: validated and
  preserved, with no graphical editor yet.
- Future proposals: a separate collection with accept/reject application operations;
  no model or AI review inbox is connected to the UI.

The editing form supports only manual maxima/minima and one-channel or global intervals.
Future fields are preserved when other annotations are modified. Custom fields belong
in `metadata`, `label.attributes`, or `extensions`; unexpected fields elsewhere are
rejected to prevent silent data loss.

Details and examples: [docs/ESQUEMA.md](docs/ESQUEMA.md),
[schema/annotations.schema.json](schema/annotations.schema.json),
[examples/demo.annotations.json](examples/demo.annotations.json).
The enriched example is synthetic; run the demo with `--demo-dir examples` to check the
initial load and table. Copy the example first if you want to preserve it: the
application saves directly over it when you edit it.

## Exporting CSV

**Export annotation CSV** writes
`recording.annotations.csv` next to the JSON file and replaces that same CSV on the next
export. It does not create JSON copies or history folders. The JSON remains the complete
source of the current state.

The CSV includes IDs, geometry, channels, seconds, indices, amplitudes/units, labels,
provenance, and model information where applicable. It includes regions and global
annotations, with empty cells where a coordinate does not apply. Global metadata,
relationships, and pending/rejected proposals remain in the JSON, not in the annotation
CSV. It is UTF-8 with a BOM; text that might be interpreted as a formula is prefixed
with a protective apostrophe. To select purely manual labels, filter by
`origin == manual`.

## Errors and data care

- Permissions: grant write access to the folder or move the **ACQ and JSON files
  together** to a writable local folder. No alternate project is silently created.
- Corruption/wrong file: preserve the original bytes and explicitly repair or restore
  the JSON. Do not delete the file merely to make the program start.
- External change: reload and repeat. States are not merged blindly.
- Residual lock after an abrupt shutdown: ensure no instance is saving, then remove the
  empty `recording.annotations.json.lock` folder.
- Saving uses a temporary file and transient lock in the same folder; these normally
  disappear when the operation finishes. They are not history or backups.

The application does not retain previous states. If you need backups, create them in
accordance with your institution's policies. This release does not read, migrate, or
delete data created by older versions that used different persistence.

## Validation and privacy

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/smoke_server.py
```

[docs/VALIDACION.md](docs/VALIDACION.md) distinguishes completed tests from pending
ones. No real user ACQ file was provided. The test browser blocks loopback access:
callbacks and HTTP/WebSocket behavior are tested, but end-to-end visual testing on the
target machine remains pending.

The server listens only on `127.0.0.1` and uses local Bokeh resources. There are no
cloud services or application telemetry. This does not provide authentication, disk
encryption, anonymization, or isolation from other processes on the same computer.
Protect names, labels, and CSV files too. Do not expose the server through tunnels or
network configuration changes.

Open only trusted ACQ files. The 12-million-sample limit is checked after decoding; the
parser is not sandboxed against malicious compressed input. Time is relative to sample
zero; acquisition pauses/segments and native AcqKnowledge markers are not reconstructed.
Compare units, sampling rates, and times against AcqKnowledge before using real
recordings.

[Design](docs/DISENO.md) · [Roadmap](docs/ROADMAP.md) · [Sources](docs/FUENTES.md)
