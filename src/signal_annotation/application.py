"""Use cases. Only the current state exists: no history, audit or undo stack."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4
from .domain import Annotation, Amplitude, Coordinate, Label, Location, Provenance, ValidationError
from .validation import text, validate_workspace


def now():
    return datetime.now(timezone.utc).isoformat()


class AnnotationService:
    def __init__(self, recording, repository, author):
        self.recording, self.repository = recording, repository
        self.author = text(author.strip(), "Author", 80)
        self.workspace = repository.load()
        validate_workspace(self.workspace, recording)

    def _commit(self, candidate):
        validate_workspace(candidate, self.recording)
        self.repository.save(candidate)
        self.workspace = candidate  # Failed writes leave visible current state untouched.

    def _manual(self, kind, channel_id, start, end, label, identity=None):
        if kind not in {"maximum", "minimum", "interval"}:
            raise ValidationError("Choose maximum, minimum, or interval.")
        if kind == "interval":
            loc = Location("interval", () if channel_id is None else (channel_id,),
                           Coordinate(start), Coordinate(end))
        else:
            ch = self.recording.channel(channel_id)
            i, seconds, value = ch.sample_at(start)
            loc = Location("point", (channel_id,), Coordinate(seconds, i), amplitude=Amplitude(ch.units, value))
            if end is not None:
                raise ValidationError("A point does not have an end time.")
        return Annotation(identity or str(uuid4()), loc, Label(kind, label.strip()), Provenance("manual", self.author, now()))

    def add(self, kind, channel_id, start, end, label):
        annotation = self._manual(kind, channel_id, start, end, label)
        candidate = deepcopy(self.workspace)
        candidate.annotations.append(annotation)
        self._commit(candidate)
        return annotation

    def edit(self, annotation_id, kind, channel_id, start, end, label):
        candidate = deepcopy(self.workspace)
        for i, old in enumerate(candidate.annotations):
            if old.id == annotation_id:
                if old.origin != "manual" or old.location.type not in {"point", "interval"}:
                    raise ValidationError("The current interface edits only manual points/intervals.")
                new = self._manual(kind, channel_id, start, end, label, old.id)
                # Keep creation provenance and extension fields; overwrite current geometry/label.
                new = replace(new, provenance=old.provenance, extensions=old.extensions,
                              label=replace(new.label, attributes=old.label.attributes))
                candidate.annotations[i] = new
                self._commit(candidate)
                return new
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
        candidate = self.repository.load()
        validate_workspace(candidate, self.recording)
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
