from typing import final
from .git_manager import GitManager
from .repository import Repository

@final
class ProjectManager(GitManager):

    @classmethod
    def repository(cls) -> Repository:
        return Repository.ProjectDirectory

