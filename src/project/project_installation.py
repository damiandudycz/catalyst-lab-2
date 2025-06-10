from typing import final
from .git_installation import GitInstallation
from .git_manager import GitManager
from .project_manager import ProjectManager

@final
class ProjectInstallation(GitInstallation):
    """Handles the full project directory installation lifecycle."""

    # Overwrite in subclassed
    @classmethod
    def manager(cls) -> GitManager:
        return ProjectManager.shared()

