from __future__ import annotations
from typing import Self, final
from dataclasses import dataclass
from .git_directory import GitDirectory
from .repository import Serializable, Repository
import uuid

@final
class ProjectDirectory(GitDirectory):

    @classmethod
    def base_location(cls) -> str:
        from .repository import Repository
        import os
        return os.path.realpath(
            os.path.expanduser(
                Repository.Settings.value.project_location
            )
        )

    @classmethod
    def parse_metadata(cls, dict: dict) -> Serializable:
        return ProjectConfiguration.init_from(data=dict)

    def initialize_metadata(self) -> ProjectConfiguration:
        if not self.metadata:
            self.metadata = ProjectConfiguration()
        return self.metadata

    def _get_by_id(self, items, target_id, attr):
        if not target_id:
            return None
        return next((item for item in items if getattr(item, attr) == target_id), None)

    def get_toolset(self) -> Toolset | None:
        if self.metadata is None:
            return None
        return self._get_by_id(Repository.Toolset.value, self.metadata.toolset_id, 'uuid')

    def get_releng_directory(self) -> RelengDirectory | None:
        if self.metadata is None:
            return None
        return self._get_by_id(Repository.RelengDirectory.value, self.metadata.releng_directory_id, 'id')

    def get_snapshot(self) -> Snapshot | None:
        if self.metadata is None:
            return None
        return self._get_by_id(Repository.Snapshot.value, self.metadata.snapshot_id, 'filename')

@dataclass
class ProjectConfiguration(Serializable):
    toolset_id: uuid.UUID | None = None
    releng_directory_id: uuid.UUID | None = None
    snapshot_id: str | None = None

    def serialize(self) -> dict:
        return {
            "toolset_id": str(self.toolset_id) if self.toolset_id else None,
            "releng_directory_id": str(self.releng_directory_id) if self.releng_directory_id else None,
            "snapshot_id": self.snapshot_id,
        }

    @classmethod
    def init_from(cls, data: dict) -> Self:
        try:
            toolset_id = uuid.UUID(data["toolset_id"]) if data.get("toolset_id") else None
            releng_directory_id = uuid.UUID(data["releng_directory_id"]) if data.get("releng_directory_id") else None
            snapshot_id = data["snapshot_id"] if data.get("snapshot_id") else None
        except KeyError:
            raise ValueError(f"Failed to parse {data}")
        return cls(
            toolset_id=toolset_id,
            releng_directory_id=releng_directory_id,
            snapshot_id=snapshot_id
        )

