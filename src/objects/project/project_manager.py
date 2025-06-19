from typing import final
from .git_manager import GitManager
from .repository import Repository
from .project_directory import ProjectDirectory
from .project_stage import ProjectStage, ProjectStageEvent
from .git_directory import GitDirectoryEvent
import os, shutil, json

@final
class ProjectManager(GitManager):

    @classmethod
    def repository(cls) -> Repository:
        return Repository.ProjectDirectory

    def is_stage_name_available(self, project: ProjectDirectory, name: str) -> bool:
        if not name:
            return False
        stage_path = project.stage_directory_path(name=name)
        return not os.path.exists(stage_path)

    def rename_stage(self, project: ProjectDirectory, stage: ProjectStage, name: str):
        if not self.is_stage_name_available(project=project, name=name):
            raise RuntimeError(f"Project name {name} is not available")
        stage_path_old = project.stage_directory_path(name=stage.name)
        stage_path_new = project.stage_directory_path(name=name)
        config_path_new = os.path.join(stage_path_new, "stage.json")
        shutil.move(stage_path_old, stage_path_new)
        stage.name = name
        config_json = stage.serialize()
        with open(config_path_new, 'w', encoding='utf-8') as f:
            json.dump(config_json, f, indent=4)
        project.event_bus.emit(GitDirectoryEvent.CONTENT_CHANGED, project)
        stage.event_bus.emit(ProjectStageEvent.NAME_CHANGED, stage)
