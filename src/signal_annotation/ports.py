"""Hexagonal boundary contracts; no framework dependency."""
from pathlib import Path
from typing import Protocol
from .domain import Proposal, Recording, Workspace
from .label_catalog import CatalogEntry, LabelCatalog

class RecordingReader(Protocol):
    def read(self, path: Path) -> Recording: ...

class WorkspaceRepository(Protocol):
    def load(self) -> Workspace: ...
    def save(self, workspace: Workspace) -> None: ...

class ProposalProvider(Protocol):
    """Future extension only. No model/detector is shipped or invoked."""
    def propose(self, recording: Recording) -> list[Proposal]: ...


class LabelCatalogRepository(Protocol):
    """Shared root-label catalog boundary; intentionally independent of the UI."""
    def load(self) -> LabelCatalog: ...
    def add(self, collection: str, name: str) -> CatalogEntry: ...
