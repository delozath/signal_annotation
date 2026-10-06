"""Semantic validation shared by use cases and persistence; standard library only."""
from datetime import datetime
import json
from math import isclose, isfinite
from .domain import ValidationError


INTERVAL_LABEL_CATALOG_KEY = "interval_label_catalog"
AMPLITUDE_LABEL_CATALOG_KEY = "amplitude_label_catalog"
GENERAL_NOTES_KEY = "general_notes"
INTERVAL_LABEL_ID_ATTRIBUTE = "interval_label_catalog_id"
INTERVAL_LABEL_NAME_ATTRIBUTE = "interval_label_catalog_name"
INTERVAL_LABEL_COLOR_ATTRIBUTE = "color"
AMPLITUDE_LABEL_ID_ATTRIBUTE = "amplitude_label_catalog_id"
AMPLITUDE_LABEL_NAME_ATTRIBUTE = "amplitude_label_catalog_name"
AMPLITUDE_LABEL_COLOR_ATTRIBUTE = "color"
AMPLITUDE_LABEL_COLORS = (
    "#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e",
    "#e6ab02", "#a6761d", "#666666", "#1f78b4", "#b2df8a",
)
INTERVAL_LABEL_COLORS = (
    "#6a3d9a", "#ff7f00", "#1b9e77", "#e7298a", "#1f78b4",
    "#b15928", "#66a61e", "#e6ab02", "#7570b3", "#d95f02",
)


def text(value, name, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValidationError(f"{name}: text between 1 and {maximum} characters is required.")
    return value


def number(value, name):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValidationError(f"{name} must be a finite number.")
    return value


def mapping(value, name):
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValidationError(f"{name} must be a JSON object.")
    try:
        if json.loads(json.dumps(value, allow_nan=False)) != value:
            raise ValueError("Not a JSON object without conversions.")
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must contain finite JSON values.")


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError
    except (TypeError, ValueError):
        raise ValidationError("An ISO 8601 date with a time zone is required.")


def validate_label(label):
    text(label.code, "Code", 80)
    text(label.text, "Label")
    mapping(label.attributes, "Label attributes")


def interval_label_catalog(metadata):
    """Validate and return the reserved interval-label catalog in metadata."""
    raw = metadata.get(INTERVAL_LABEL_CATALOG_KEY, [])
    if not isinstance(raw, list):
        raise ValidationError("Interval label catalog must be a list.")
    if len(raw) > len(INTERVAL_LABEL_COLORS):
        raise ValidationError(f"At most {len(INTERVAL_LABEL_COLORS)} interval labels may be registered.")
    names, colors, result = set(), set(), []
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {"name", "color"}:
            raise ValidationError("Each interval label catalog entry requires only name and color.")
        name = text(entry["name"], "Interval label name")
        if name != name.strip():
            raise ValidationError("Interval label names may not start or end with whitespace.")
        normalized = name.casefold()
        if normalized in names:
            raise ValidationError("Interval label names must be unique.")
        color = entry["color"]
        if color not in INTERVAL_LABEL_COLORS or color in colors:
            raise ValidationError("Interval label colors must be distinct predefined colors.")
        names.add(normalized)
        colors.add(color)
        result.append({"name": name, "color": color})
    return tuple(result)


def amplitude_label_catalog(metadata):
    """Validate and return the reserved amplitude-label catalog in metadata."""
    raw = metadata.get(AMPLITUDE_LABEL_CATALOG_KEY, [])
    if not isinstance(raw, list):
        raise ValidationError("Amplitude label catalog must be a list.")
    if len(raw) > len(AMPLITUDE_LABEL_COLORS):
        raise ValidationError(f"At most {len(AMPLITUDE_LABEL_COLORS)} amplitude labels may be registered.")
    names, colors, result = set(), set(), []
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {"name", "color"}:
            raise ValidationError("Each amplitude label catalog entry requires only name and color.")
        name = text(entry["name"], "Amplitude label name")
        if name != name.strip():
            raise ValidationError("Amplitude label names may not start or end with whitespace.")
        normalized = name.casefold()
        if normalized in names:
            raise ValidationError("Amplitude label names must be unique.")
        color = entry["color"]
        if color not in AMPLITUDE_LABEL_COLORS or color in colors:
            raise ValidationError("Amplitude label colors must be distinct predefined colors.")
        names.add(normalized)
        colors.add(color)
        result.append({"name": name, "color": color})
    return tuple(result)


def general_notes(metadata):
    """Validate and return the reserved recording-level free-text notes."""
    raw = metadata.get(GENERAL_NOTES_KEY, "")
    if not isinstance(raw, str):
        raise ValidationError("General recording notes must be a string.")
    return raw


def validate_catalog_snapshot(attributes, *, id_attribute, name_attribute,
                              color_attribute, colors, label_name):
    """Validate one self-contained external-catalog reference and its snapshots."""
    identity = text(attributes.get(id_attribute), f"{label_name} catalog ID", 100)
    if identity != identity.strip():
        raise ValidationError(f"{label_name} catalog ID may not start or end with whitespace.")
    name = text(attributes.get(name_attribute), f"{label_name} name")
    if name != name.strip():
        raise ValidationError(f"{label_name} names may not start or end with whitespace.")
    color = attributes.get(color_attribute)
    if color not in colors:
        raise ValidationError(f"{label_name} color must be a predefined color.")
    return name, color


def validate_location(loc, recording):
    if loc.type not in {"point", "interval", "region", "recording"}:
        raise ValidationError("Unknown location type.")
    if not isinstance(loc.channel_ids, tuple) or len(set(loc.channel_ids)) != len(loc.channel_ids):
        raise ValidationError("Repeated channels or invalid channel collection.")
    channels = [recording.channel(cid) for cid in loc.channel_ids]
    limit = min(c.duration for c in channels) if channels else recording.duration
    if loc.type == "recording":
        if channels or any(x is not None for x in (loc.start, loc.end, loc.amplitude)):
            raise ValidationError("A recording location covers the whole recording, with no coordinates or channels.")
        return
    if loc.start is None:
        raise ValidationError("Start coordinate is missing.")
    for coordinate in (loc.start, loc.end):
        if coordinate is None:
            continue
        number(coordinate.seconds, "Time")
        if not 0 <= coordinate.seconds <= limit:
            raise ValidationError("Coordinate is outside the recording/channel.")
        if coordinate.sample_index is not None:
            i = coordinate.sample_index
            if len(channels) != 1 or type(i) is not int or not 0 <= i < len(channels[0].values):
                raise ValidationError("A sample index requires exactly one channel and a valid integer.")
            if not isclose(coordinate.seconds, i / channels[0].sample_rate, rel_tol=0, abs_tol=1e-10):
                raise ValidationError("Sample index and seconds do not match.")
    if loc.type == "point":
        if len(channels) != 1 or loc.end is not None or loc.amplitude is None:
            raise ValidationError("A point requires one channel, time, and amplitude, with no end.")
        amp = loc.amplitude
        number(amp.value, "Amplitude")
        if amp.minimum is not None or amp.maximum is not None:
            raise ValidationError("A point amplitude does not have a range.")
        if loc.start.sample_index is not None:
            _, _, actual = channels[0].sample_at(loc.start.seconds)
            if not isclose(amp.value, actual, rel_tol=1e-12, abs_tol=1e-12):
                raise ValidationError("The amplitude does not match the original sample.")
    else:
        if loc.end is None or not loc.start.seconds < loc.end.seconds:
            raise ValidationError("start < end is required.")
        if loc.type == "interval" and loc.amplitude is not None:
            raise ValidationError("A time interval does not have an amplitude.")
        if loc.type == "region":
            if len(channels) != 1 or loc.amplitude is None:
                raise ValidationError("A time-amplitude region requires one channel and an amplitude range.")
            amp = loc.amplitude
            number(amp.minimum, "Minimum amplitude")
            number(amp.maximum, "Maximum amplitude")
            if amp.value is not None or not amp.minimum < amp.maximum:
                raise ValidationError("Invalid amplitude range.")
    if loc.amplitude is not None and loc.amplitude.units != channels[0].units:
        raise ValidationError("The units do not match the channel.")


def validate_workspace(workspace, recording):
    if workspace.recording_id != recording.id:
        raise ValidationError("The JSON belongs to another ACQ file.")
    mapping(workspace.metadata, "Metadata")
    mapping(workspace.extensions, "Extensions")
    catalog = interval_label_catalog(workspace.metadata)
    amplitude_catalog = amplitude_label_catalog(workspace.metadata)
    general_notes(workspace.metadata)
    catalog_by_name = {entry["name"]: entry for entry in catalog}
    amplitude_catalog_by_name = {entry["name"]: entry for entry in amplitude_catalog}
    collections = (workspace.annotations, workspace.proposals, workspace.relationships)
    if any(not isinstance(items, list) for items in collections):
        raise ValidationError("Collections must be lists.")
    ids = [item.id for items in collections for item in items]
    if len(set(ids)) != len(ids):
        raise ValidationError("Duplicate IDs.")
    for identity in ids:
        text(identity, "ID", 100)
    by_id = {p.id: p for p in workspace.proposals}
    for proposal in workspace.proposals:
        validate_location(proposal.location, recording)
        validate_label(proposal.label)
        text(proposal.model_name, "Model")
        text(proposal.model_version, "Version")
        timestamp(proposal.created_at)
        mapping(proposal.extensions, "Proposal extensions")
        if proposal.confidence is not None and not 0 <= number(proposal.confidence, "Confidence") <= 1:
            raise ValidationError("Confidence is outside [0,1].")
        if proposal.status == "pending":
            if proposal.reviewed_by is not None or proposal.reviewed_at is not None:
                raise ValidationError("Pending proposal has inconsistent review data.")
        elif proposal.status in {"accepted", "rejected"}:
            text(proposal.reviewed_by, "Reviewer", 80)
            timestamp(proposal.reviewed_at)
        else:
            raise ValidationError("Unknown proposal status.")
    for annotation in workspace.annotations:
        validate_location(annotation.location, recording)
        validate_label(annotation.label)
        attributes = annotation.label.attributes
        catalog_name = attributes.get(INTERVAL_LABEL_NAME_ATTRIBUTE)
        if INTERVAL_LABEL_ID_ATTRIBUTE in attributes:
            snapshot_name, _ = validate_catalog_snapshot(
                attributes, id_attribute=INTERVAL_LABEL_ID_ATTRIBUTE,
                name_attribute=INTERVAL_LABEL_NAME_ATTRIBUTE,
                color_attribute=INTERVAL_LABEL_COLOR_ATTRIBUTE,
                colors=INTERVAL_LABEL_COLORS, label_name="Interval label")
            if (annotation.location.type != "interval" or annotation.label.code != "interval" or
                    annotation.label.text != snapshot_name):
                raise ValidationError("The interval label does not match its external catalog snapshot.")
        elif catalog_name is not None:
            entry = catalog_by_name.get(catalog_name)
            if (annotation.location.type != "interval" or entry is None or
                    annotation.label.text != catalog_name or
                    attributes.get(INTERVAL_LABEL_COLOR_ATTRIBUTE) != entry["color"]):
                raise ValidationError("The interval label does not match its catalog entry.")
        amplitude_catalog_name = attributes.get(AMPLITUDE_LABEL_NAME_ATTRIBUTE)
        if AMPLITUDE_LABEL_ID_ATTRIBUTE in attributes:
            snapshot_name, _ = validate_catalog_snapshot(
                attributes, id_attribute=AMPLITUDE_LABEL_ID_ATTRIBUTE,
                name_attribute=AMPLITUDE_LABEL_NAME_ATTRIBUTE,
                color_attribute=AMPLITUDE_LABEL_COLOR_ATTRIBUTE,
                colors=AMPLITUDE_LABEL_COLORS, label_name="Amplitude label")
            if (annotation.location.type != "point" or annotation.label.code != "marker" or
                    annotation.label.text != snapshot_name):
                raise ValidationError("The amplitude label does not match its external catalog snapshot.")
        elif amplitude_catalog_name is not None:
            entry = amplitude_catalog_by_name.get(amplitude_catalog_name)
            if (annotation.location.type != "point" or annotation.label.code != "marker" or
                    entry is None or annotation.label.text != amplitude_catalog_name or
                    attributes.get(AMPLITUDE_LABEL_COLOR_ATTRIBUTE) != entry["color"]):
                raise ValidationError("The amplitude label does not match its catalog entry.")
        mapping(annotation.extensions, "Annotation extensions")
        p = annotation.provenance
        text(p.author, "Author", 80)
        timestamp(p.created_at)
        if p.origin == "manual":
            if p.proposal_id is not None:
                raise ValidationError("A manual label does not originate from a proposal.")
        elif p.origin == "model_reviewed":
            proposal = by_id.get(p.proposal_id)
            if proposal is None or proposal.status != "accepted" or proposal.reviewed_by != p.author or proposal.location != annotation.location or proposal.label != annotation.label:
                raise ValidationError("The annotation does not match its accepted proposal/reviewer.")
        else:
            raise ValidationError("Unknown provenance.")
    annotation_ids = {a.id for a in workspace.annotations}
    channel_ids = {c.id for c in recording.channels}
    for relation in workspace.relationships:
        validate_label(relation.label)
        mapping(relation.extensions, "Relationship extensions")
        for endpoint in (relation.source, relation.target):
            valid = annotation_ids if endpoint.type == "annotation" else channel_ids if endpoint.type == "channel" else set()
            if endpoint.id not in valid:
                raise ValidationError("Relationship contains a missing reference or unknown type.")
