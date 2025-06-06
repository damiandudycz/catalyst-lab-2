from typing import final
from .git_installation import GitInstallation
from .git_manager import GitManager
from .releng_manager import RelengManager

@final
class RelengInstallation(GitInstallation):
    """Handles the full releng directory installation lifecycle."""

    # Overwrite in subclassed
    @classmethod
    def manager(cls) -> GitManager:
        return RelengManager.shared()

    def __init__(self, name: str):
        super().__init__(
            name=name,
            repository_url="https://github.com/gentoo/releng.git"
        )

