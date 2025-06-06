from typing import final
from .git_installation import GitInstallation
from .git_manager import GitManager
from .overlay_manager import OverlayManager

@final
class OverlayInstallation(GitInstallation):
    """Handles the full overlay directory installation lifecycle."""

    # Overwrite in subclassed
    @classmethod
    def manager(cls) -> GitManager:
        return OverlayManager.shared()

    def __init__(self, name: str, repository_url: str | None):
        super().__init__(
            name=name,
            repository_url=repository_url
        )

