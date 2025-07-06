from typing import final
from .git_installation import GitInstallation, GitDirectorySetupConfiguration
from .git_manager import GitManager
from .project_manager import ProjectManager
from .toolset import Toolset
from .releng_directory import RelengDirectory
from .snapshot import Snapshot
from .project_directory import ProjectConfiguration
from .architecture import Architecture
from .multistage_process import (
    MultiStageProcess, MultiStageProcessStage,
    MultiStageProcessState, MultiStageProcessStageState
)

@final
class ProjectInstallation(GitInstallation):
    """Handles the full project directory installation lifecycle."""

    def __init__(
        self,
        source_config: GitDirectorySetupConfiguration,
        toolset: Toolset,
        releng_directory: RelengDirectory,
        snapshot: Snapshot,
        architecture: Architecture
    ):
        self.toolset = toolset
        self.releng_directory = releng_directory
        self.snapshot = snapshot
        self.architecture = architecture
        super().__init__(configuration=source_config)

    # Overwrite in subclassed
    @classmethod
    def manager(cls) -> GitManager:
        return ProjectManager.shared()

    def setup_stages(self):
        super().setup_stages()
        self.stages.append(
            ProjectInstallationStepSaveConfig(
                multistage_process=self,
                toolset=self.toolset,
                releng_directory=self.releng_directory,
                snapshot=self.snapshot,
                architecture=self.architecture
            )
        )


class ProjectInstallationStepSaveConfig(MultiStageProcessStage):
    def __init__(
        self,
        multistage_process: MultiStageProcess,
        toolset: Toolset,
        releng_directory: RelengDirectory,
        snapshot: Snapshot,
        architecture: Architecture
    ):
        super().__init__(
            name="Save configuration",
            description="Stores metadata about selected components",
            multistage_process=multistage_process
        )
        self.toolset = toolset
        self.releng_directory = releng_directory
        self.snapshot = snapshot
        self.architecture = architecture
    def start(self):
        super().start()
        try:
            self.multistage_process.directory.metadata = ProjectConfiguration(
                toolset_id=self.toolset.uuid,
                releng_directory_id=self.releng_directory.id,
                snapshot_id=self.snapshot.filename,
                architecture=self.architecture
            )
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during '{self.name}': {e}")
            self.complete(MultiStageProcessStageState.FAILED)

