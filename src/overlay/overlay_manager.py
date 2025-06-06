from typing import Type, final
from .git_manager import GitManager
from .git_directory import GitDirectory
from .overlay_directory import OverlayDirectory
from .repository import Repository

@final
class OverlayManager(GitManager):

    @classmethod
    def repository(cls) -> Repository:
        return Repository.OverlayDirectory

