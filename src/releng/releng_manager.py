from typing import Type
from .git_manager import GitManager
from .git_directory import GitDirectory
from .releng_directory import RelengDirectory
from .repository import Repository

class RelengManager(GitManager):

    @classmethod
    def repository(cls) -> Repository:
        return Repository.RelengDirectory

    @classmethod
    def item_class(cls) -> Type[GitDirectory]:
        return RelengDirectory
