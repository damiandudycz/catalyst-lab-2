from .git_update import GitUpdate
from .git_manager import GitManager
from .releng_manager import RelengManager
from .releng_directory import RelengDirectory

# ------------------------------------------------------------------------------
# Releng installation.
# ------------------------------------------------------------------------------

class RelengUpdate(GitUpdate):
    """Handles the full releng directory installation lifecycle."""

    # Overwrite in subclassed
    @classmethod
    def manager(cls) -> GitManager:
        return RelengManager.shared()

