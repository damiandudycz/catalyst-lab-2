from __future__ import annotations
from typing import Self, final
from dataclasses import dataclass
from .git_directory import GitDirectory, GitDirectoryEvent
from .repository import Serializable, Repository
from .project_stage import ProjectStage
from .stages_tree_view import TreeNode
import uuid, json, os

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

    @property
    def stages(self) -> list[ProjectStage]:
        if not hasattr(self, '_stages'):
            self._stages = []
            stages_dir = os.path.join(self.directory_path(), "stages")
            for item in os.listdir(stages_dir):
                stage_path = os.path.join(stages_dir, item)
                config_path = os.path.join(stage_path, "stage.json")
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        stage = ProjectStage.init_from(data=data)
                        self._stages.append(stage)
                except Exception as e:
                    print(f"Failed to load stage from {config_path}: {e}")
        return self._stages
    @stages.setter
    def stages(self, value: list[ProjectStage]):
        self._stages = value

    def add_stage(self, stage: ProjectStage):
        self.stages.append(stage)
        self.event_bus.emit(GitDirectoryEvent.CONTENT_CHANGED, self._stages)

    def stages_tree(self) -> list[dict]:
        """Builds a tree of stages for seeds inheritance."""
        stage_nodes = {stage.id: TreeNode(value=stage) for stage in self.stages}
        roots = []
        for stage_id, node in stage_nodes.items():
            parent_id = node.value.parent_id
            if parent_id and parent_id in stage_nodes:
                stage_nodes[parent_id].children.append(node)
            else:
                roots.append(node)
        return roots

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

    def install_stage(self, stage: ProjectStage):
        # Save stage details in project directory
        from .project_manager import ProjectManager
        if not ProjectManager.shared().is_stage_name_available(project=self, name=stage.name):
            raise RuntimeError(f"Stage with name {stage.name} already exists in this project.")
        project_path = self.directory_path()
        stage_path = os.path.join(project_path, "stages", stage.name)
        config_path = os.path.join(stage_path, "stage.json")
        os.makedirs(stage_path, exist_ok=False)
        config_json = stage.serialize()
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_json, f, indent=4)

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

