"""Atomic storage for the project-root shared label catalog."""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import warnings

from ..domain import ValidationError
from ..label_catalog import LabelCatalog, catalog_from_dict, catalog_to_dict


class CatalogStorageError(ValidationError):
    pass


class CatalogConflictError(CatalogStorageError):
    pass


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"Duplicate JSON key: {key}.")
        result[key] = value
    return result


def decode_catalog(content: bytes) -> LabelCatalog:
    try:
        raw = json.loads(content, object_pairs_hook=_unique_keys,
                         parse_constant=lambda value: (_ for _ in ()).throw(ValidationError(f"Invalid JSON number: {value}.")))
        return catalog_from_dict(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError("Corrupt label catalog JSON. It was not overwritten.") from exc


def atomic_catalog_text(path: Path, content: str) -> None:
    """Write a flushed, fsynced same-directory temporary file and atomically replace."""
    temp = None
    try:
        fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError as exc:
        raise CatalogStorageError(f"Could not save {path}. The change was not committed: {exc}") from exc
    finally:
        if temp is not None:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
            except OSError as exc:
                warnings.warn(f"Could not clean up temporary file {temp}: {exc}", RuntimeWarning)


class JsonLabelCatalogRepository:
    """A one-file root catalog with in-memory fingerprints for stale-write protection."""
    def __init__(self, project_root: Path):
        self.path = Path(project_root) / "label_catalog.json"
        self._catalog: LabelCatalog | None = None
        self._fingerprint: str | None = None

    @contextmanager
    def _lock(self):
        lock = self.path.with_name(self.path.name + ".lock")
        try:
            lock.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise CatalogConflictError(f"Catalog write in progress or residual lock: {lock}.") from exc
        except OSError as exc:
            raise CatalogStorageError(f"Cannot write next to the catalog: {exc}") from exc
        try:
            yield
        finally:
            try:
                lock.rmdir()
            except OSError as exc:
                warnings.warn(f"Operation completed, but lock {lock} could not be removed: {exc}", RuntimeWarning)

    def _read(self) -> tuple[LabelCatalog, str]:
        if self.path.is_symlink():
            raise CatalogStorageError("The label catalog cannot be a symbolic link.")
        try:
            content = self.path.read_bytes()
        except OSError as exc:
            raise CatalogStorageError(f"Could not read {self.path}: {exc}") from exc
        return decode_catalog(content), sha256(content).hexdigest()

    def load(self) -> LabelCatalog:
        if not self.path.exists() and not self.path.is_symlink():
            self._catalog, self._fingerprint = LabelCatalog(), None
            return self._catalog
        self._catalog, self._fingerprint = self._read()
        return self._catalog

    def add(self, collection: str, name: str):
        if self._catalog is None:
            self.load()
        assert self._catalog is not None
        candidate, entry = self._catalog.add(collection, name)
        content = json.dumps(catalog_to_dict(candidate), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with self._lock():
            exists = self.path.exists() or self.path.is_symlink()
            if exists:
                _, current = self._read()
                if self._fingerprint is None or current != self._fingerprint:
                    raise CatalogConflictError("The label catalog changed in another program. Reload and repeat the action. It was not overwritten.")
            elif self._fingerprint is not None:
                raise CatalogConflictError("The label catalog was deleted in another program. Reload before adding a label.")
            atomic_catalog_text(self.path, content)
            self._catalog = candidate
            self._fingerprint = sha256(content.encode("utf-8")).hexdigest()
        return entry
