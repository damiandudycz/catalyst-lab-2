from __future__ import annotations
from typing import Self, final
from dataclasses import dataclass
from .git_directory import GitDirectory, GitDirectoryEvent
from .repository import Serializable, Repository
from .project_stage import ProjectStage
from .stages_tree_view import TreeNode
from .architecture import Architecture
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
        self.event_bus.emit(GitDirectoryEvent.CONTENT_CHANGED, self)

    def stages_tree(self) -> list[dict]:
        """Builds a tree of stages for seeds inheritance."""
        stage_nodes = {stage.id: TreeNode(value=stage) for stage in self.stages}
        roots = []
        for stage_id, node in stage_nodes.items():
            parent_id = node.value.parent
            if parent_id and parent_id in stage_nodes:
                stage_nodes[parent_id].children.append(node)
            else:
                roots.append(node)
        return roots

    @property
    def builds_summary(self) -> str:
        """Short description of project stage builds, eg. for builds list."""
        from .project_build import load_project_builds
        from .project_build_process import running_project_build
        builds = load_project_builds(self)
        summary = f"{len(builds)} build{'s' if len(builds) != 1 else ''}, last {builds[0].date.strftime('%Y-%m-%d %H:%M')}" if builds else "No builds yet"
        return f"Building now · {summary}" if running_project_build(self) else summary

    @property
    def configuration_error(self) -> str | None:
        """Why project can't be built because of its configuration (toolset, releng directory, snapshot)."""
        if self.get_toolset() is None:
            return "Toolset is not selected or was removed"
        if self.get_releng_directory() is None:
            return "Releng directory is not selected or was removed"
        if self.get_snapshot() is None:
            return "Snapshot is not selected or was removed"
        return None

    def _last_build_run_result(self):
        result, building, _ = self._last_build_run()
        return result, building

    def _last_build_run(self):
        """Result of newest build run, whether project is being built, and timestamp of newest run."""
        from .project_build import last_build_run
        from .project_build_process import running_project_build
        running = running_project_build(self)
        timestamp, result = last_build_run(self, running_timestamp=running.timestamp if running else None)
        return result, running is not None, timestamp

    def mark_build_result_seen(self):
        """Failure of newest build run was seen (project was opened), it's not shown in projects list anymore."""
        _, _, timestamp = self._last_build_run()
        if timestamp is None or self.metadata is None or self.metadata.seen_build_timestamp == timestamp:
            return
        self.metadata.seen_build_timestamp = timestamp
        from .repository import Repository
        Repository.ProjectDirectory.save()
        from .event_bus import SharedEvent
        self.event_bus.emit(SharedEvent.STATE_UPDATED, self)

    @property
    def build_status_indicator_values(self):
        """Builds list: last build run completed (succeeded), failed (error) or stopped (warning), blinking while
        building."""
        from .project_build import BuildRunResult
        from .status_indicator import StatusIndicatorState, StatusDetail, item_status
        result, building = self._last_build_run_result()
        match result:
            case BuildRunResult.FAILED: state, description = StatusIndicatorState.ERROR, "Last build failed"
            case BuildRunResult.STOPPED: state, description = StatusIndicatorState.WARNING, "Last build was stopped"
            case BuildRunResult.COMPLETED: state, description = StatusIndicatorState.SUCCEEDED, "Last build completed"
            case _: state, description = StatusIndicatorState.IDLE, "No builds"
        return item_status([StatusDetail(state, description),
                            StatusDetail(StatusIndicatorState.LOADED, "Building") if building else None], blinking=building)

    @property
    def status_indicator_values(self):
        """Projects list: configuration error, failed last build (until project is opened), Git status (changes,
        errors), blinking while building."""
        from .project_build import BuildRunResult
        from .status_indicator import StatusIndicatorState, StatusDetail, item_status
        result, building, timestamp = self._last_build_run()
        details = []
        if configuration_error := self.configuration_error:
            details.append(StatusDetail(StatusIndicatorState.ERROR, configuration_error))
        if result == BuildRunResult.FAILED and (self.metadata is None or self.metadata.seen_build_timestamp != timestamp):
            details.append(StatusDetail(StatusIndicatorState.ERROR, "Last build failed"))
        details += self.status_details()
        if building:
            details.append(StatusDetail(StatusIndicatorState.LOADED, "Building"))
        return item_status(details, blinking=building or self.is_busy)

    @property
    def deploy_summary(self) -> str:
        """Builds of project that can be deployed, for Deploy section."""
        from .project_build import load_project_builds
        from .deploy_installation import is_deployable
        builds = [build for build in load_project_builds(self) if is_deployable(self, build)]
        if not builds:
            return "No stage3 or stage4 builds"
        return f"{len(builds)} build{'s' if len(builds) != 1 else ''} ready to deploy, last {builds[0].date.strftime('%Y-%m-%d %H:%M')}"

    @property
    def deploy_status_indicator_values(self):
        """Blinking indicator while build of project is being deployed."""
        from .deploy_installation import DeployInstallation
        from .multistage_process import MultiStageProcess, MultiStageProcessState
        from .status_indicator import StatusIndicatorState, StatusDetail, item_status
        deploying = any(process.project_id == self.id and process.status == MultiStageProcessState.IN_PROGRESS
                        for process in MultiStageProcess.get_started_processes_by_class(DeployInstallation))
        return item_status([StatusDetail(StatusIndicatorState.LOADED, "Deploying") if deploying else None], blinking=deploying)

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

    def get_architecture(self) -> Architecture | None:
        if self.metadata is None:
            return None
        return self.metadata.architecture

    # TODO: Maybe move this and some other methods to project manager?
    @classmethod
    def stage_directory_path_for_name(cls, name: str, project: ProjectDirectory) -> str:
        project_path = project.directory_path()
        return os.path.join(
            project_path, "stages",
            cls.sanitized_name_for_name(name)
        )

    def stage_directory_path(self, name: str) -> str:
        return ProjectDirectory.stage_directory_path_for_name(name=name, project=self)

    def install_stage(self, stage: ProjectStage):
        # Save stage details in project directory
        from .project_manager import ProjectManager
        if not ProjectManager.shared().is_stage_name_available(project=self, name=stage.name):
            raise RuntimeError(f"Stage with name {stage.name} already exists in this project.")
        ProjectManager.shared().save_stage(project=self, stage=stage)

@dataclass
class ProjectConfiguration(Serializable):
    toolset_id: uuid.UUID | None = None
    releng_directory_id: uuid.UUID | None = None
    snapshot_id: str | None = None
    architecture: Architecture | None = None
    seen_build_timestamp: str | None = None # Newest build run whose result was seen (project was opened).

    def serialize(self) -> dict:
        return {
            "toolset_id": str(self.toolset_id) if self.toolset_id else None,
            "releng_directory_id": str(self.releng_directory_id) if self.releng_directory_id else None,
            "snapshot_id": self.snapshot_id,
            "architecture": self.architecture.value if self.architecture else None,
            "seen_build_timestamp": self.seen_build_timestamp,
        }

    @classmethod
    def init_from(cls, data: dict) -> Self:
        try:
            toolset_id = uuid.UUID(data["toolset_id"]) if data.get("toolset_id") else None
            releng_directory_id = uuid.UUID(data["releng_directory_id"]) if data.get("releng_directory_id") else None
            snapshot_id = data["snapshot_id"] if data.get("snapshot_id") else None
            architecture = Architecture(data["architecture"]) if data.get("architecture") else None
        except KeyError:
            raise ValueError(f"Failed to parse {data}")
        return cls(
            toolset_id=toolset_id,
            releng_directory_id=releng_directory_id,
            snapshot_id=snapshot_id,
            architecture=architecture,
            seen_build_timestamp=data.get("seen_build_timestamp")
        )

