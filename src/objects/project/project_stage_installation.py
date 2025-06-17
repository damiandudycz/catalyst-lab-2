from typing import final
from abc import ABC, abstractmethod
from .git_installation import GitInstallation, GitDirectorySetupConfiguration
from .git_manager import GitManager
from .toolset import Toolset
from .releng_directory import RelengDirectory
from .snapshot import Snapshot
from .project_directory import ProjectDirectory, ProjectConfiguration
from .project_stage import ProjectStage
from .multistage_process import (
    MultiStageProcess, MultiStageProcessStage,
    MultiStageProcessState, MultiStageProcessStageState
)
import uuid

@final
class ProjectStageInstallation(MultiStageProcess, ABC):
    def __init__(
        self,
        project_directory: ProjectDirectory,
        target_name: str,
        releng_template_name: str | None,
        stage_name: str,
        parent_id: uuid.UUID | None
    ):
        self.project_directory = project_directory
        self.target_name = target_name
        self.releng_template_name = releng_template_name
        self.stage_name = stage_name
        self.parent_id = parent_id
        super().__init__(title="Project stage installation")

    def name(self) -> str:
        return self.target_name

    def setup_stages(self):
        self.stages.append(
            ProjectStageInstallationStepCreate(
                project_directory=self.project_directory,
                target_name=self.target_name,
                releng_template_name=self.releng_template_name,
                stage_name=self.stage_name,
                parent_id=self.parent_id,
                multistage_process=self
            )
        )

    def complete_process(self, success: bool):
        if success:
            self.project_directory.stages.append(self.stage)
            self.project_directory.update_status()

# ------------------------------------------------------------------------------
# Installation process steps.
# ------------------------------------------------------------------------------

class ProjectStageInstallationStepCreate(MultiStageProcessStage):
    def __init__(
        self,
        project_directory: ProjectDirectory,
        target_name: str,
        releng_template_name: str | None,
        stage_name: str,
        parent_id: uuid.UUID | None,
        multistage_process: MultiStageProcess
    ):
        super().__init__(
            name="Create project stage",
            description="Saves stage configuration in project directory",
            multistage_process=multistage_process
        )
        self.project_directory = project_directory
        self.target_name = target_name
        self.releng_template_name = releng_template_name
        self.stage_name = stage_name
        self.parent_id = parent_id
        self.process_started = False
    def start(self):
        super().start()
        self.process_started = True
        try:
            stage = ProjectStage(id=None, parent_id=self.parent_id, name=self.stage_name, target_name=self.target_name, releng_template_name=self.releng_template_name)
            self.project_directory.install_stage(stage=stage)
            self.multistage_process.stage = stage
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during '{self.name}': {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        return True

