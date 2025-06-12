from typing import final
from .git_installation import GitInstallation, GitDirectorySetupConfiguration
from .git_manager import GitManager
from .project_manager import ProjectManager
from .toolset import Toolset
from .releng_directory import RelengDirectory
from .snapshot import Snapshot

@final
class ProjectInstallation(GitInstallation):
    """Handles the full project directory installation lifecycle."""

    def __init__(
        self,
        source_config: GitDirectorySetupConfiguration,
        toolset: Toolset,
        releng_directory: RelengDirectory,
        snapshot: Snapshot
    ):
        super().__init__(configuration=source_config)
        self.toolset = toolset
        self.releng_directory = releng_directory
        self.snapshot = snapshot

    # Overwrite in subclassed
    @classmethod
    def manager(cls) -> GitManager:
        return ProjectManager.shared()

