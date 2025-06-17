from typing import final
from .git_manager import GitManager
from .repository import Repository
from .project_directory import ProjectDirectory
import os

@final
class ProjectManager(GitManager):

    @classmethod
    def repository(cls) -> Repository:
        return Repository.ProjectDirectory

    def is_stage_name_available(self, project: ProjectDirectory, name: str) -> bool:
        if not name:
            return False
        project_path = project.directory_path()
        stage_path = os.path.join(project_path, "stages", name)
        return not os.path.exists(stage_path)

