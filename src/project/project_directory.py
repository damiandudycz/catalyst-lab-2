from __future__ import annotations
from typing import final
from .git_directory import GitDirectory
from .repository import Serializable
from typing import Self
from dataclasses import dataclass
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

@dataclass
class ProjectConfiguration(Serializable):
    toolset_id: uuid.UUID | None = None
    releng_directory_id: uuid.UUID | None = None
    snapshot_id: str | None = None

    def serialize(self) -> dict:
        dict = {
            "toolset_id": str(self.toolset_id) if self.toolset_id else None,
            "releng_directory_id": str(self.releng_directory_id) if self.releng_directory_id else None,
            "snapshot_id": self.snapshot_id, # TODO: Store ID also in snapshot and use instead of filename
        }
        return {
            "toolset_id": str(self.toolset_id) if self.toolset_id else None,
            "releng_directory_id": str(self.releng_directory_id) if self.releng_directory_id else None,
            "snapshot_id": self.snapshot_id, # TODO: Store ID also in snapshot and use instead of filename
        }

    @classmethod
    def init_from(cls, data: dict) -> Self:
        try:
            toolset_id = uuid.UUID(data["toolset_id"]) if data.get("toolset_id") else None
            releng_directory_id = uuid.UUID(data["releng_directory_id"]) if data.get("releng_directory_id") else None
            snapshot_id = data["snapshot_id"] if data.get("snapshot_id") else None # TODO ^
        except KeyError:
            raise ValueError(f"Failed to parse {data}")
        return cls(
            toolset_id=toolset_id,
            releng_directory_id=releng_directory_id,
            snapshot_id=snapshot_id
        )

