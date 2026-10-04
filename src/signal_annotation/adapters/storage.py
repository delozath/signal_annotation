"""One current-state JSON sidecar. No database, audit, history or snapshots."""
from contextlib import contextmanager
import csv
from dataclasses import asdict
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import tempfile
import warnings
from ..domain import (Amplitude, Annotation, Coordinate, Label, Location, Proposal,
                      Provenance, Reference, Relationship, ValidationError, Workspace)
from ..validation import validate_workspace

SCHEMA_VERSION = 2  # Serialization format only; never incremented by edits.


class StorageError(ValidationError):
    pass


class ConflictError(StorageError):
    pass


def sidecar_path(acq_path: Path) -> Path:
    return Path(acq_path).expanduser().resolve().with_suffix(".annotations.json")


def recording_metadata(recording):
    return {"id": recording.id, "file_name": recording.name,
            "file_size_bytes": recording.source_size_bytes, "synthetic": recording.synthetic,
            "time_basis": "relative_seconds_from_sample_zero",
            "channels": [{"id": c.id, "name": c.name, "units": c.units,
                          "sample_rate_hz": c.sample_rate, "sample_count": len(c.values)} for c in recording.channels]}


def to_dict(workspace, recording):
    validate_workspace(workspace, recording)
    payload = {"schema_version": SCHEMA_VERSION, "recording": recording_metadata(recording),
            "annotations": [asdict(a) for a in workspace.annotations],
            "proposals": [asdict(p) for p in workspace.proposals],
            "relationships": [asdict(r) for r in workspace.relationships],
            "metadata": workspace.metadata, "extensions": workspace.extensions}
    return json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))


def require_keys(obj, keys):
    if not isinstance(obj, dict) or set(obj) != set(keys):
        raise ValidationError("Missing/unknown fields. Use extensions for custom fields.")
    return obj


def parse_label(raw):
    require_keys(raw, ["code", "text", "attributes"])
    return Label(**raw)


def parse_location(raw):
    require_keys(raw, ["type", "channel_ids", "start", "end", "amplitude"])
    if not isinstance(raw["channel_ids"], list) or any(not isinstance(x, str) for x in raw["channel_ids"]):
        raise ValidationError("channel_ids must be a list of IDs.")
    def coordinate(value):
        if value is None: return None
        require_keys(value, ["seconds", "sample_index"])
        return Coordinate(**value)
    amp = raw["amplitude"]
    if amp is not None:
        require_keys(amp, ["units", "value", "minimum", "maximum"])
        amp = Amplitude(**amp)
    return Location(raw["type"], tuple(raw["channel_ids"]), coordinate(raw["start"]), coordinate(raw["end"]), amp)


def from_dict(raw, recording):
    try:
        require_keys(raw, ["schema_version", "recording", "annotations", "proposals", "relationships", "metadata", "extensions"])
        if type(raw["schema_version"]) is not int or raw["schema_version"] != SCHEMA_VERSION:
            raise ValidationError("Unsupported format version.")
        meta = raw["recording"]
        expected = recording_metadata(recording)
        require_keys(meta, expected)
        if type(meta["synthetic"]) is not bool or (meta["file_size_bytes"] is not None and type(meta["file_size_bytes"]) is not int) or not isinstance(meta["channels"], list):
            raise ValidationError("Invalid ACQ metadata types.")
        for channel in meta["channels"]:
            require_keys(channel, ["id", "name", "units", "sample_rate_hz", "sample_count"])
            if type(channel["sample_count"]) is not int or isinstance(channel["sample_rate_hz"], bool) or not isinstance(channel["sample_rate_hz"], (int, float)):
                raise ValidationError("Invalid channel metadata types.")
        if meta != expected:
            raise ValidationError("The JSON does not match the ACQ file (SHA-256, name, size, or channels). It was not modified.")
        for key in ("annotations", "proposals", "relationships"):
            if not isinstance(raw[key], list):
                raise ValidationError(f"{key} must be a list.")
        annotations, proposals, relationships = [], [], []
        for item in raw["annotations"]:
            require_keys(item, ["id", "location", "label", "provenance", "extensions"])
            require_keys(item["provenance"], ["origin", "author", "created_at", "proposal_id"])
            annotations.append(Annotation(item["id"], parse_location(item["location"]), parse_label(item["label"]), Provenance(**item["provenance"]), item["extensions"]))
        for item in raw["proposals"]:
            require_keys(item, Proposal.__dataclass_fields__)
            proposals.append(Proposal(**{**item, "location": parse_location(item["location"]), "label": parse_label(item["label"])}))
        for item in raw["relationships"]:
            require_keys(item, ["id", "source", "target", "label", "extensions"])
            require_keys(item["source"], ["type", "id"])
            require_keys(item["target"], ["type", "id"])
            relationships.append(Relationship(item["id"], Reference(**item["source"]), Reference(**item["target"]), parse_label(item["label"]), item["extensions"]))
        workspace = Workspace(recording.id, annotations, proposals, relationships, raw["metadata"], raw["extensions"])
        validate_workspace(workspace, recording)
        return workspace
    except (TypeError, KeyError, AttributeError) as exc:
        raise ValidationError("Invalid JSON structure; the file was not overwritten.") from exc


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValidationError(f"Duplicate JSON key: {key}.")
        result[key] = value
    return result


def decode(content, recording):
    try:
        raw = json.loads(content, object_pairs_hook=_unique_keys,
                         parse_constant=lambda x: (_ for _ in ()).throw(ValidationError(f"Invalid JSON number: {x}.")))
        return from_dict(raw, recording)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError("Corrupt JSON. Preserve the file and explicitly repair/restore it; it was not overwritten.") from exc


def atomic_text(path, content):
    """Same-directory fsync + atomic replace; no backups or persistent temp files."""
    path = Path(path)
    temp = None
    try:
        fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content.encode("utf-8") if isinstance(content, str) else content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError as exc:
        raise StorageError(f"Could not save {path}. Check folder write permissions, free space, and that no program is locking the file. The change was not committed: {exc}") from exc
    finally:
        if temp is not None:
            try: os.unlink(temp)
            except FileNotFoundError: pass
            except OSError as exc:
                warnings.warn(f"Could not clean up temporary file {temp}: {exc}", RuntimeWarning)


class JsonSidecarRepository:
    """One repository instance per session; content fingerprint remains only in RAM."""
    def __init__(self, path, recording):
        self.path, self.recording = Path(path), recording
        self._fingerprint = None

    @contextmanager
    def _lock(self):
        # Transient lock directory, not a second data file. No waiting/stale overwrite.
        lock = self.path.with_name(self.path.name + ".lock")
        try:
            lock.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise ConflictError(f"Write in progress or residual lock: {lock}. Close other instances; only if none is saving, delete that empty folder and retry.") from exc
        except OSError as exc:
            raise StorageError(f"Cannot write next to the ACQ file: {self.path.parent}. Grant folder permissions or move the ACQ and JSON files together to a writable local folder: {exc}") from exc
        try:
            yield
        finally:
            try:
                lock.rmdir()
            except OSError as exc:
                # A cleanup failure must not turn a committed write into a false
                # failure or leave the service's current state behind the file.
                warnings.warn(f"Operation completed, but lock {lock} could not be removed. Close other instances and delete that empty folder before saving again: {exc}", RuntimeWarning)

    def _read(self):
        try:
            if self.path.is_symlink():
                raise StorageError("The sidecar cannot be a symbolic link. Use a regular JSON file next to the ACQ file.")
            content = self.path.read_bytes()
        except OSError as exc:
            raise StorageError(f"Could not read {self.path}. Check permissions and that the file exists: {exc}") from exc
        return decode(content, self.recording), sha256(content).hexdigest()

    def load(self):
        with self._lock():
            if not self.path.exists() and not self.path.is_symlink():
                empty = Workspace(self.recording.id)
                content = json.dumps(to_dict(empty, self.recording), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
                atomic_text(self.path, content)
            workspace, fingerprint = self._read()
            self._fingerprint = fingerprint
            return workspace

    def save(self, workspace):
        content = json.dumps(to_dict(workspace, self.recording), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with self._lock():
            _, current = self._read()  # Refuse corrupt/mismatched files even after startup.
            if self._fingerprint is None or current != self._fingerprint:
                raise ConflictError("The JSON changed in another tab/program. Click Reload and repeat the action. It was not overwritten.")
            atomic_text(self.path, content)
            self._fingerprint = sha256(content.encode("utf-8")).hexdigest()


def csv_safe(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_annotations_csv(workspace, recording, path):
    """Explicit current annotation export. No duplicate JSON, history or snapshots."""
    validate_workspace(workspace, recording)
    fields = ["recording_id", "id", "geometry", "channel_ids", "start_seconds", "start_sample_index", "end_seconds", "end_sample_index", "amplitude", "amplitude_min", "amplitude_max", "units", "label_code", "label_text", "origin", "author", "created_at", "proposal_id", "model_name", "model_version", "location_json", "label_attributes_json", "extensions_json"]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    proposals = {p.id: p for p in workspace.proposals}
    for a in workspace.annotations:
        loc, prov = a.location, a.provenance
        amp = loc.amplitude
        proposal = proposals.get(prov.proposal_id)
        row = dict(recording_id=recording.id, id=a.id, geometry=loc.type, channel_ids=";".join(loc.channel_ids),
                   start_seconds=loc.start.seconds if loc.start else None, start_sample_index=loc.start.sample_index if loc.start else None,
                   end_seconds=loc.end.seconds if loc.end else None, end_sample_index=loc.end.sample_index if loc.end else None,
                   amplitude=amp.value if amp else None, amplitude_min=amp.minimum if amp else None,
                   amplitude_max=amp.maximum if amp else None, units=amp.units if amp else None,
                   label_code=a.label.code, label_text=a.label.text, origin=prov.origin, author=prov.author,
                   created_at=prov.created_at, proposal_id=prov.proposal_id,
                   model_name=proposal.model_name if proposal else None, model_version=proposal.model_version if proposal else None,
                   location_json=json.dumps(asdict(loc), ensure_ascii=False), label_attributes_json=json.dumps(a.label.attributes, ensure_ascii=False),
                   extensions_json=json.dumps(a.extensions, ensure_ascii=False))
        writer.writerow({k: csv_safe(v) for k, v in row.items()})
    atomic_text(Path(path), "\ufeff" + buffer.getvalue())
    return Path(path)
