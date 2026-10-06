"""Use cases. Only the current state exists: no history, audit or undo stack."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4
from .domain import Annotation, Amplitude, Coordinate, Label, Location, Provenance, ValidationError
from .validation import (AMPLITUDE_LABEL_CATALOG_KEY, AMPLITUDE_LABEL_COLOR_ATTRIBUTE,
                         AMPLITUDE_LABEL_COLORS, AMPLITUDE_LABEL_ID_ATTRIBUTE,
                         AMPLITUDE_LABEL_NAME_ATTRIBUTE,
                         GENERAL_NOTES_KEY, INTERVAL_LABEL_CATALOG_KEY,
                         INTERVAL_LABEL_COLOR_ATTRIBUTE, INTERVAL_LABEL_COLORS,
                         INTERVAL_LABEL_ID_ATTRIBUTE,
                         INTERVAL_LABEL_NAME_ATTRIBUTE, amplitude_label_catalog,
                         interval_label_catalog, text, validate_workspace)


# These colors are deliberately fixed: a saved generic marker keeps the same color
# after a reload, and old point annotations can be given a stable display color.
MARKER_COLOR_ATTRIBUTE = AMPLITUDE_LABEL_COLOR_ATTRIBUTE
MARKER_COLORS = AMPLITUDE_LABEL_COLORS

LEGACY_INTERVAL_COLOR = "#2d927d"


def point_colors(annotations):
    """Return deterministic palette assignments for all active point annotations.

    Persisted valid colors take precedence, including repeated colors for points that
    share a catalog label. Points from older sidecars, including legacy maxima/minima,
    receive a stable color by annotation order without changing their on-disk labels.
    """
    points = [a for a in annotations if a.location.type == "point"]
    assigned = {}
    for index, annotation in enumerate(points):
        color = annotation.label.attributes.get(MARKER_COLOR_ATTRIBUTE)
        assigned[annotation.id] = (color if color in MARKER_COLORS else
                                   MARKER_COLORS[index % len(MARKER_COLORS)])
    return assigned


def interval_color(annotation, workspace):
    """Return a persisted catalog color or the stable legacy interval color."""
    if annotation.location.type != "interval":
        raise ValidationError("Only intervals have interval-label colors.")
    attributes = annotation.label.attributes
    if INTERVAL_LABEL_ID_ATTRIBUTE in attributes:
        color = attributes.get(INTERVAL_LABEL_COLOR_ATTRIBUTE)
        if (attributes.get(INTERVAL_LABEL_NAME_ATTRIBUTE) == annotation.label.text and
                color in INTERVAL_LABEL_COLORS):
            return color
    catalog_name = attributes.get(INTERVAL_LABEL_NAME_ATTRIBUTE)
    for entry in interval_label_catalog(workspace.metadata):
        if (entry["name"] == catalog_name == annotation.label.text and
                entry["color"] == attributes.get(INTERVAL_LABEL_COLOR_ATTRIBUTE)):
            return entry["color"]
    return LEGACY_INTERVAL_COLOR


def now():
    return datetime.now(timezone.utc).isoformat()


class AnnotationService:
    def __init__(self, recording, repository, author, catalog_repository=None):
        self.recording, self.repository = recording, repository
        self.author = text(author.strip(), "Author", 80)
        self.catalog_repository = catalog_repository
        # Load the external catalog first so a corrupt catalog cannot cause an absent
        # annotation sidecar to be created as a side effect of opening the service.
        catalog = catalog_repository.load() if catalog_repository is not None else None
        workspace = repository.load()
        validate_workspace(workspace, recording)
        self.catalog = catalog
        self.workspace = workspace

    @staticmethod
    def _entry_dict(entry):
        return {"id": entry.id, "name": entry.name, "color": entry.color}

    def _external_entry(self, collection, catalog_id, message):
        if not isinstance(catalog_id, str):
            raise ValidationError(message)
        # Each annotation command resolves against current durable catalog content;
        # corrupt, deleted, or replaced catalogs cannot silently reuse a cached entry.
        catalog = self.catalog_repository.load()
        entry = next((item for item in catalog.collection(collection)
                      if item.id == catalog_id), None)
        if entry is None:
            raise ValidationError(message)
        return catalog, entry

    def _refresh_catalog_after_add(self):
        """Cache the catalog that the repository actually committed.

        The repository may have been used elsewhere since this service last loaded it.
        Reconstructing from ``self.catalog`` would then omit durable entries (and can
        assign a different palette position).  A post-commit load is read-only.
        """
        self.catalog = self.catalog_repository.load()

    def _commit(self, candidate):
        validate_workspace(candidate, self.recording)
        self.repository.save(candidate)
        self.workspace = candidate  # Failed writes leave visible current state untouched.

    def _manual(self, kind, channel_id, start, end, label, identity=None, attributes=None):
        # ``maximum`` and ``minimum`` are kept for existing sidecars and callers.
        # New UI-created points use the generic ``marker`` code and copy their
        # selected catalog name into ``Label.text``.
        if kind not in {"marker", "maximum", "minimum", "interval"}:
            raise ValidationError("Choose an amplitude marker or interval.")
        if kind == "interval":
            loc = Location("interval", () if channel_id is None else (channel_id,),
                           Coordinate(start), Coordinate(end))
        else:
            ch = self.recording.channel(channel_id)
            i, seconds, value = ch.sample_at(start)
            loc = Location("point", (channel_id,), Coordinate(seconds, i), amplitude=Amplitude(ch.units, value))
            if end is not None:
                raise ValidationError("A point does not have an end time.")
        if kind == "marker":
            label = text(label, "Marker name")
        return Annotation(identity or str(uuid4()), loc, Label(kind, label.strip(), attributes or {}),
                          Provenance("manual", self.author, now()))

    def add(self, kind, channel_id, start, end, label):
        annotation = self._manual(kind, channel_id, start, end, label)
        candidate = deepcopy(self.workspace)
        candidate.annotations.append(annotation)
        self._commit(candidate)
        return annotation

    def add_amplitude_label(self, name):
        """Append one unique amplitude label to its independent persistent catalog."""
        if self.catalog_repository is not None:
            entry = self.catalog_repository.add("amplitude_labels", name)
            self._refresh_catalog_after_add()
            return self._entry_dict(entry)
        name = text(name, "Amplitude label name").strip()
        candidate = deepcopy(self.workspace)
        catalog = list(amplitude_label_catalog(candidate.metadata))
        if any(entry["name"].casefold() == name.casefold() for entry in catalog):
            raise ValidationError("Amplitude label names must be unique.")
        if len(catalog) >= len(AMPLITUDE_LABEL_COLORS):
            raise ValidationError(f"At most {len(AMPLITUDE_LABEL_COLORS)} amplitude labels may be registered.")
        used = {entry["color"] for entry in catalog}
        entry = {"name": name, "color": next(color for color in AMPLITUDE_LABEL_COLORS
                                               if color not in used)}
        catalog.append(entry)
        candidate.metadata[AMPLITUDE_LABEL_CATALOG_KEY] = catalog
        self._commit(candidate)
        return entry

    def add_catalog_marker(self, channel_id, start, catalog_id):
        """Create a point whose semantic label is selected from the amplitude catalog."""
        resolved_catalog = None
        if self.catalog_repository is not None:
            resolved_catalog, external_entry = self._external_entry(
                "amplitude_labels", catalog_id, "Choose a registered amplitude label.")
            entry = self._entry_dict(external_entry)
        else:
            if not isinstance(catalog_id, str):
                raise ValidationError("Choose a registered amplitude label.")
            entry = next((item for item in amplitude_label_catalog(self.workspace.metadata)
                          if item["name"] == catalog_id), None)
            if entry is None:
                raise ValidationError("Choose a registered amplitude label.")
        attributes = {AMPLITUDE_LABEL_NAME_ATTRIBUTE: entry["name"],
                      AMPLITUDE_LABEL_COLOR_ATTRIBUTE: entry["color"]}
        if "id" in entry:
            attributes[AMPLITUDE_LABEL_ID_ATTRIBUTE] = entry["id"]
        annotation = self._manual("marker", channel_id, start, None, entry["name"],
                                  attributes=attributes)
        candidate = deepcopy(self.workspace)
        candidate.annotations.append(annotation)
        self._commit(candidate)
        if resolved_catalog is not None:
            self.catalog = resolved_catalog
        return annotation

    def add_interval_label(self, name):
        """Append one unique interval label to the independent persistent catalog."""
        if self.catalog_repository is not None:
            entry = self.catalog_repository.add("interval_labels", name)
            self._refresh_catalog_after_add()
            return self._entry_dict(entry)
        name = text(name, "Interval label name").strip()
        candidate = deepcopy(self.workspace)
        catalog = list(interval_label_catalog(candidate.metadata))
        if any(entry["name"].casefold() == name.casefold() for entry in catalog):
            raise ValidationError("Interval label names must be unique.")
        if len(catalog) >= len(INTERVAL_LABEL_COLORS):
            raise ValidationError(f"At most {len(INTERVAL_LABEL_COLORS)} interval labels may be registered.")
        used = {entry["color"] for entry in catalog}
        entry = {"name": name, "color": next(color for color in INTERVAL_LABEL_COLORS
                                                if color not in used)}
        catalog.append(entry)
        candidate.metadata[INTERVAL_LABEL_CATALOG_KEY] = catalog
        self._commit(candidate)
        return entry

    def save_general_notes(self, notes):
        """Persist recording-level free text without changing other metadata."""
        if not isinstance(notes, str):
            raise ValidationError("General recording notes must be a string.")
        candidate = deepcopy(self.workspace)
        # An empty string is the stable, explicit on-disk state for cleared notes.
        candidate.metadata[GENERAL_NOTES_KEY] = notes
        self._commit(candidate)

    def add_catalog_interval(self, channel_id, start, end, catalog_id):
        """Create an interval whose semantic label is selected from the catalog."""
        resolved_catalog = None
        if self.catalog_repository is not None:
            resolved_catalog, external_entry = self._external_entry(
                "interval_labels", catalog_id, "Choose a registered interval label.")
            entry = self._entry_dict(external_entry)
        else:
            if not isinstance(catalog_id, str):
                raise ValidationError("Choose a registered interval label.")
            entry = next((item for item in interval_label_catalog(self.workspace.metadata)
                          if item["name"] == catalog_id), None)
            if entry is None:
                raise ValidationError("Choose a registered interval label.")
        attributes = {INTERVAL_LABEL_NAME_ATTRIBUTE: entry["name"],
                      INTERVAL_LABEL_COLOR_ATTRIBUTE: entry["color"]}
        if "id" in entry:
            attributes[INTERVAL_LABEL_ID_ATTRIBUTE] = entry["id"]
        annotation = self._manual("interval", channel_id, start, end, entry["name"],
                                  attributes=attributes)
        candidate = deepcopy(self.workspace)
        candidate.annotations.append(annotation)
        self._commit(candidate)
        if resolved_catalog is not None:
            self.catalog = resolved_catalog
        return annotation

    def edit(self, annotation_id, kind, channel_id, start, end, label):
        candidate = deepcopy(self.workspace)
        for i, old in enumerate(candidate.annotations):
            if old.id == annotation_id:
                if old.origin != "manual" or old.location.type not in {"point", "interval"}:
                    raise ValidationError("The current interface edits only manual points/intervals.")
                attributes = old.label.attributes
                new = self._manual(kind, channel_id, start, end, label, old.id, attributes)
                # Keep creation provenance and extension fields; overwrite current geometry/label.
                new = replace(new, provenance=old.provenance, extensions=old.extensions)
                candidate.annotations[i] = new
                self._commit(candidate)
                return new
        raise ValidationError("Unknown annotation.")

    def move_point(self, annotation_id, start):
        """Move one manual point to the real channel sample nearest ``start``."""
        candidate = deepcopy(self.workspace)
        for i, old in enumerate(candidate.annotations):
            if old.id != annotation_id:
                continue
            if old.location.type != "point" or old.origin != "manual":
                raise ValidationError("Only manual point annotations can be moved.")
            channel = self.recording.channel(old.channel_id)
            sample_index, seconds, value = channel.sample_at(start)
            location = replace(old.location, start=Coordinate(seconds, sample_index),
                               amplitude=Amplitude(channel.units, value))
            moved = replace(old, location=location)
            candidate.annotations[i] = moved
            self._commit(candidate)
            return moved
        raise ValidationError("Unknown annotation.")

    def resize_interval(self, annotation_id, endpoint, seconds):
        """Move one boundary of a manual interval and persist the current state."""
        if endpoint not in {"start", "end"}:
            raise ValidationError("Choose the start or end of the interval.")
        candidate = deepcopy(self.workspace)
        for i, old in enumerate(candidate.annotations):
            if old.id != annotation_id:
                continue
            if old.location.type != "interval" or old.origin != "manual":
                raise ValidationError("Only manual interval annotations can be resized.")
            # A dragged continuous boundary no longer claims an exact sample.  The
            # untouched boundary, label, provenance, extensions, and list position
            # remain byte-for-byte equivalent in the serialized model.
            location = replace(old.location, **{endpoint: Coordinate(seconds)})
            resized = replace(old, location=location)
            candidate.annotations[i] = resized
            self._commit(candidate)
            return resized
        raise ValidationError("Unknown annotation.")

    def reassign_point_label(self, annotation_id, catalog_id):
        """Assign one manual generic point to an existing amplitude catalog entry."""
        resolved_catalog = None
        if self.catalog_repository is not None:
            resolved_catalog, external_entry = self._external_entry(
                "amplitude_labels", catalog_id, "Choose a registered amplitude label.")
            entry = self._entry_dict(external_entry)
        else:
            if not isinstance(catalog_id, str):
                raise ValidationError("Choose a registered amplitude label.")
            entry = next((item for item in amplitude_label_catalog(self.workspace.metadata)
                          if item["name"] == catalog_id), None)
            if entry is None:
                raise ValidationError("Choose a registered amplitude label.")
        candidate = deepcopy(self.workspace)
        for i, old in enumerate(candidate.annotations):
            if old.id != annotation_id:
                continue
            if old.location.type != "point" or old.label.code != "marker" or old.origin != "manual":
                raise ValidationError("Only manual generic point annotations can be reassigned.")
            attributes = {**old.label.attributes,
                          AMPLITUDE_LABEL_NAME_ATTRIBUTE: entry["name"],
                          AMPLITUDE_LABEL_COLOR_ATTRIBUTE: entry["color"]}
            if "id" in entry:
                attributes[AMPLITUDE_LABEL_ID_ATTRIBUTE] = entry["id"]
            else:
                attributes.pop(AMPLITUDE_LABEL_ID_ATTRIBUTE, None)
            candidate.annotations[i] = replace(
                old, label=replace(old.label, text=entry["name"], attributes=attributes))
            self._commit(candidate)
            if resolved_catalog is not None:
                self.catalog = resolved_catalog
            return candidate.annotations[i]
        raise ValidationError("Unknown annotation.")

    def delete(self, annotation_ids):
        ids = set(annotation_ids)
        candidate = deepcopy(self.workspace)
        if not ids or not ids <= {a.id for a in candidate.annotations}:
            raise ValidationError("Select existing annotations.")
        if any(r.source.type == "annotation" and r.source.id in ids or r.target.type == "annotation" and r.target.id in ids for r in candidate.relationships):
            raise ValidationError("The annotation has relationships. Explicitly remove those relationships before deleting it.")
        candidate.annotations = [a for a in candidate.annotations if a.id not in ids]
        self._commit(candidate)

    def reload(self):
        catalog = self.catalog_repository.load() if self.catalog_repository is not None else None
        candidate = self.repository.load()
        validate_workspace(candidate, self.recording)
        self.catalog = catalog
        self.workspace = candidate

    def register_proposals(self, proposals):
        if any(p.status != "pending" for p in proposals):
            raise ValidationError("New proposals must be pending.")
        candidate = deepcopy(self.workspace)
        candidate.proposals.extend(proposals)
        self._commit(candidate)

    def review_proposal(self, proposal_id, accept):
        candidate = deepcopy(self.workspace)
        for i, proposal in enumerate(candidate.proposals):
            if proposal.id == proposal_id:
                if proposal.status != "pending":
                    raise ValidationError("The proposal has already been reviewed.")
                candidate.proposals[i] = replace(proposal, status="accepted" if accept else "rejected", reviewed_by=self.author, reviewed_at=now())
                if accept:
                    candidate.annotations.append(Annotation(str(uuid4()), proposal.location, proposal.label,
                        Provenance("model_reviewed", self.author, now(), proposal.id)))
                self._commit(candidate)
                return
        raise ValidationError("Unknown proposal.")
