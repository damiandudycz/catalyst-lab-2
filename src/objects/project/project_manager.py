from typing import final
from .git_manager import GitManager
from .repository import Repository
from .project_directory import ProjectDirectory
from .project_stage import ProjectStage, ProjectStageEvent
from .git_directory import GitDirectoryEvent
import os, shutil, json, uuid

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
        shutil.move(stage_path_old, stage_path_new)
        stage.name = name
        self.save_stage(project=project, stage=stage)
        stage.event_bus.emit(ProjectStageEvent.NAME_CHANGED, stage)

    def change_stage_target(self, project: ProjectDirectory, stage: ProjectStage, target_name: str):
        stage.target_name = target_name
        stage.releng_template_name = None
        self.save_stage(project=project, stage=stage)

    def change_stage_parent(self, project: ProjectDirectory, stage: ProjectStage, parent_id: uuid.UUID | None):
        stage.parent_id = parent_id
        self.save_stage(project=project, stage=stage)

    def change_stage_releng_template(self, project: ProjectDirectory, stage: ProjectStage, releng_template_name: str):
        stage.releng_template_name = releng_template_name
        self.save_stage(project=project, stage=stage)

    def save_stage(self, project: ProjectDirectory, stage: ProjectStage):
        stage_path = project.stage_directory_path(name=stage.name)
        config_path = os.path.join(stage_path, "stage.json")
        config_json = stage.serialize()
        os.makedirs(stage_path, exist_ok=True)
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_json, f, indent=4)
        project.event_bus.emit(GitDirectoryEvent.CONTENT_CHANGED, project)
