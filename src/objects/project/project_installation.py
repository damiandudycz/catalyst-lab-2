from typing import final
import subprocess
from .git_installation import GitInstallation, GitDirectorySetupConfiguration, GitDirectorySource
from .git_manager import GitManager
from .project_manager import ProjectManager
from .toolset import Toolset
from .releng_directory import RelengDirectory
from .snapshot import Snapshot
from .project_directory import ProjectConfiguration
from .architecture import Architecture
from .project_template import apply_project_template, load_cloned_template
import os, shutil
from .multistage_process import (
    MultiStageProcess, MultiStageProcessStage,
    MultiStageProcessState, MultiStageProcessStageState
)

@final
class ProjectInstallation(GitInstallation):
    """Handles the full project directory installation lifecycle."""

    def __init__(
        self,
        source_config: GitDirectorySetupConfiguration,
        toolset: Toolset,
        releng_directory: RelengDirectory,
        snapshot: Snapshot,
        architecture: Architecture
    ):
        self.toolset = toolset
        self.releng_directory = releng_directory
        self.snapshot = snapshot
        self.architecture = architecture
        super().__init__(configuration=source_config)

    # Overwrite in subclassed
    @classmethod
    def manager(cls) -> GitManager:
        return ProjectManager.shared()

    def setup_stages(self):
        super().setup_stages()
        save_config = ProjectInstallationStepSaveConfig(
            multistage_process=self,
            toolset=self.toolset,
            releng_directory=self.releng_directory,
            snapshot=self.snapshot,
            architecture=self.architecture
        )
        if self.configuration.source == GitDirectorySource.TEMPLATE:
            # Stages of template get default values from configuration, so it's saved before. Content is created before
            # Git repository is configured, so new repository has it in first commit.
            self.stages[1:1] = [save_config, ProjectInstallationStepApplyTemplate(multistage_process=self)]
        else:
            self.stages.append(save_config)


class ProjectInstallationStepSaveConfig(MultiStageProcessStage):
    def __init__(
        self,
        multistage_process: MultiStageProcess,
        toolset: Toolset,
        releng_directory: RelengDirectory,
        snapshot: Snapshot,
        architecture: Architecture
    ):
        super().__init__(
            name="Save configuration",
            description="Stores metadata about selected components",
            multistage_process=multistage_process
        )
        self.toolset = toolset
        self.releng_directory = releng_directory
        self.snapshot = snapshot
        self.architecture = architecture
    def start(self):
        super().start()
        try:
            self.multistage_process.directory.metadata = ProjectConfiguration(
                toolset_id=self.toolset.uuid,
                releng_directory_id=self.releng_directory.id,
                snapshot_id=self.snapshot.filename,
                architecture=self.architecture
            )
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during '{self.name}': {e}")
            self.complete(MultiStageProcessStageState.FAILED)

class ProjectInstallationStepApplyTemplate(MultiStageProcessStage):
    def __init__(self, multistage_process: MultiStageProcess):
        super().__init__(
            name="Apply template",
            description="Creates stages and files of selected template",
            multistage_process=multistage_process
        )
    def start(self):
        super().start()
        try:
            directory = self.multistage_process.directory
            selection = self.multistage_process.configuration.data
            template = selection.template
            temporary_directory = None
            if selection.repository_url:
                # Options were read from template.toml downloaded alone, files come from cloned repository.
                template, temporary_directory = load_cloned_template(directory.directory_path(), selection.repository_url,
                                                                     template.repository_path)
            try:
                names = template.resolve(selection.selected, project_name=directory.name)
                self.log(f"Template: {template.name}")
                for variable in template.visible_variables(names):
                    self.log(f"{variable.title}: {names[variable.id]}")
                # Cloned template repository is replaced with generated content, its history is kept.
                apply_project_template(project_directory=directory, template=template, names=names, log=self.log,
                                       replace_content=selection.repository_url is not None)
            finally:
                if temporary_directory:
                    shutil.rmtree(temporary_directory, ignore_errors=True)
            if selection.repository_url:
                path = directory.directory_path()
                self._run(["git", "-C", path, "add", "--all"])
                if subprocess.run(["git", "-C", path, "diff", "--cached", "--quiet"]).returncode != 0:
                    self._run(["git", "-C", path, "commit", "--quiet", "-m", f"Create project from template {template.name}"])
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            self.log(f"Error: {e}")
            print(f"Error during '{self.name}': {e}")
            self.complete(MultiStageProcessStageState.FAILED)

    def _run(self, command: list[str]):
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in result.stdout.splitlines():
            self.log(line)
        if result.returncode != 0:
            raise RuntimeError(f"{' '.join(command)} failed")
