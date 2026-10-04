"""Small domain model. Standard library only; no UI, parser or persistence imports."""
from dataclasses import dataclass, field
from math import floor, isfinite
from typing import Sequence


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    units: str
    sample_rate: float
    values: Sequence[float]

    def __post_init__(self):
        if not isfinite(self.sample_rate) or self.sample_rate <= 0 or not len(self.values):
            raise ValidationError("Empty channel or invalid sampling rate.")

    @property
    def duration(self):
        return (len(self.values) - 1) / self.sample_rate

    def sample_at(self, t):
        if not isinstance(t, (int, float)) or isinstance(t, bool) or not 0 <= t <= self.duration or not isfinite(t):
            raise ValidationError("Time is outside the channel.")
        i = min(len(self.values) - 1, floor(t * self.sample_rate + .5))
        value = float(self.values[i])
        if not isfinite(value):
            raise ValidationError("The sample does not have a finite value.")
        return i, i / self.sample_rate, value


@dataclass(frozen=True)
class Recording:
    id: str
    name: str
    channels: tuple[Channel, ...]
    synthetic: bool = False
    source_size_bytes: int | None = None

    def __post_init__(self):
        if not self.channels or len({c.id for c in self.channels}) != len(self.channels):
            raise ValidationError("The recording requires channels with unique IDs.")
        if self.duration <= 0:
            raise ValidationError("The recording requires more than one time sample.")

    @property
    def duration(self):
        return max(c.duration for c in self.channels)

    def channel(self, channel_id):
        for ch in self.channels:
            if ch.id == channel_id:
                return ch
        raise ValidationError(f"Unknown channel: {channel_id!r}.")


@dataclass(frozen=True)
class Coordinate:
    seconds: float
    sample_index: int | None = None


@dataclass(frozen=True)
class Amplitude:
    units: str
    value: float | None = None
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class Location:
    type: str  # point | interval | region | recording
    channel_ids: tuple[str, ...] = ()  # empty means whole recording / all channels
    start: Coordinate | None = None
    end: Coordinate | None = None
    amplitude: Amplitude | None = None


@dataclass(frozen=True)
class Label:
    code: str  # maximum/minimum/event/artifact/... independent of geometry
    text: str
    attributes: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Provenance:
    origin: str  # manual | model_reviewed
    author: str
    created_at: str
    proposal_id: str | None = None


@dataclass(frozen=True)
class Annotation:
    id: str
    location: Location
    label: Label
    provenance: Provenance
    extensions: dict = field(default_factory=dict)

    # Read-only conveniences for the minimal plotting adapter; not serialized.
    @property
    def channel_id(self):
        return self.location.channel_ids[0] if self.location.channel_ids else None
    @property
    def kind(self):
        return self.label.code if self.location.type == "point" else self.location.type
    @property
    def start(self):
        return self.location.start.seconds if self.location.start else None
    @property
    def end(self):
        return self.location.end.seconds if self.location.end else None
    @property
    def value(self):
        return self.location.amplitude.value if self.location.amplitude else None
    @property
    def origin(self):
        return self.provenance.origin
    @property
    def author(self):
        return self.provenance.author


@dataclass(frozen=True)
class Proposal:
    id: str
    location: Location
    label: Label
    model_name: str
    model_version: str
    confidence: float | None
    created_at: str
    status: str = "pending"
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    extensions: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Reference:
    type: str  # annotation | channel
    id: str


@dataclass(frozen=True)
class Relationship:
    id: str
    source: Reference
    target: Reference
    label: Label
    extensions: dict = field(default_factory=dict)


@dataclass
class Workspace:
    recording_id: str
    annotations: list[Annotation] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    extensions: dict = field(default_factory=dict)
