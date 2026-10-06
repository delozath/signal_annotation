"""Framework-independent shared label catalog model and validation."""
from dataclasses import dataclass
from uuid import uuid4

from .domain import ValidationError


SCHEMA_VERSION = 1
MAX_LABELS_PER_COLLECTION = 10
AMPLITUDE_LABEL_COLORS = (
    "#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e",
    "#e6ab02", "#a6761d", "#666666", "#1f78b4", "#b2df8a",
)
INTERVAL_LABEL_COLORS = (
    "#6a3d9a", "#ff7f00", "#1b9e77", "#e7298a", "#1f78b4",
    "#b15928", "#66a61e", "#e6ab02", "#7570b3", "#d95f02",
)
COLLECTIONS = ("amplitude_labels", "interval_labels")
_PALETTES = {
    "amplitude_labels": AMPLITUDE_LABEL_COLORS,
    "interval_labels": INTERVAL_LABEL_COLORS,
}


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    name: str
    color: str


@dataclass(frozen=True)
class LabelCatalog:
    amplitude_labels: tuple[CatalogEntry, ...] = ()
    interval_labels: tuple[CatalogEntry, ...] = ()

    def collection(self, name: str) -> tuple[CatalogEntry, ...]:
        if name not in COLLECTIONS:
            raise ValidationError("Unknown label catalog collection.")
        return getattr(self, name)

    def add(self, collection: str, name: str, *, identity: str | None = None) -> tuple["LabelCatalog", CatalogEntry]:
        entries = self.collection(collection)
        clean_name = normalize_name(name)
        if len(entries) >= MAX_LABELS_PER_COLLECTION:
            raise ValidationError(f"At most {MAX_LABELS_PER_COLLECTION} labels may be registered in each collection.")
        if clean_name.casefold() in {entry.name.casefold() for entry in entries}:
            raise ValidationError("Label catalog names must be unique within their collection.")
        color = next((item for item in _PALETTES[collection] if item not in {entry.color for entry in entries}), None)
        if color is None:
            raise ValidationError("Label catalog colors must be distinct within their collection.")
        entry = CatalogEntry(identity or str(uuid4()), clean_name, color)
        validate_entry(entry, collection, {item.id for item in self.amplitude_labels + self.interval_labels})
        replacement = entries + (entry,)
        if collection == "amplitude_labels":
            result = LabelCatalog(replacement, self.interval_labels)
        else:
            result = LabelCatalog(self.amplitude_labels, replacement)
        validate_catalog(result)
        return result, entry


def normalize_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("Label catalog name must be text.")
    value = value.strip()
    if not value or len(value) > 200:
        raise ValidationError("Label catalog name must contain between 1 and 200 characters.")
    return value


def validate_entry(entry: CatalogEntry, collection: str, known_ids: set[str] | None = None) -> None:
    if collection not in COLLECTIONS:
        raise ValidationError("Unknown label catalog collection.")
    if not isinstance(entry.id, str) or not entry.id.strip() or entry.id != entry.id.strip() or len(entry.id) > 100:
        raise ValidationError("Label catalog ID must be a bounded nonblank string.")
    if entry.name != normalize_name(entry.name):
        raise ValidationError("Label catalog names must be trimmed.")
    if entry.color not in _PALETTES[collection]:
        raise ValidationError("Label catalog color is not an allowed predefined color.")
    if known_ids is not None and entry.id in known_ids:
        raise ValidationError("Label catalog IDs must be unique.")


def validate_catalog(catalog: LabelCatalog) -> LabelCatalog:
    if not isinstance(catalog, LabelCatalog):
        raise ValidationError("Invalid label catalog.")
    all_ids: set[str] = set()
    for collection in COLLECTIONS:
        entries = catalog.collection(collection)
        if not isinstance(entries, tuple) or len(entries) > MAX_LABELS_PER_COLLECTION:
            raise ValidationError(f"At most {MAX_LABELS_PER_COLLECTION} labels may be registered in each collection.")
        names: set[str] = set()
        colors: set[str] = set()
        for entry in entries:
            if not isinstance(entry, CatalogEntry):
                raise ValidationError("Invalid label catalog entry.")
            validate_entry(entry, collection, all_ids)
            all_ids.add(entry.id)
            folded = entry.name.casefold()
            if folded in names:
                raise ValidationError("Label catalog names must be unique within their collection.")
            if entry.color in colors:
                raise ValidationError("Label catalog colors must be distinct within their collection.")
            names.add(folded)
            colors.add(entry.color)
    return catalog


def catalog_to_dict(catalog: LabelCatalog) -> dict:
    validate_catalog(catalog)
    return {
        "schema_version": SCHEMA_VERSION,
        "amplitude_labels": [entry.__dict__.copy() for entry in catalog.amplitude_labels],
        "interval_labels": [entry.__dict__.copy() for entry in catalog.interval_labels],
    }


def catalog_from_dict(raw: object) -> LabelCatalog:
    if not isinstance(raw, dict) or set(raw) != {"schema_version", *COLLECTIONS}:
        raise ValidationError("Label catalog has missing or unknown fields.")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != SCHEMA_VERSION:
        raise ValidationError("Unsupported label catalog schema version.")
    parsed: dict[str, tuple[CatalogEntry, ...]] = {}
    for collection in COLLECTIONS:
        values = raw[collection]
        if not isinstance(values, list):
            raise ValidationError("Label catalog collections must be lists.")
        entries = []
        for value in values:
            if not isinstance(value, dict) or set(value) != {"id", "name", "color"}:
                raise ValidationError("Each label catalog entry requires only id, name, and color.")
            entries.append(CatalogEntry(value["id"], value["name"], value["color"]))
        parsed[collection] = tuple(entries)
    return validate_catalog(LabelCatalog(**parsed))
