from abc import ABC, abstractmethod
from .multistage_process import (
    MultiStageProcess, MultiStageProcessStage,
    MultiStageProcessState, MultiStageProcessStageState,
    MultiStageProcessEvent, MultiStageProcessStageEvent,
)
from .git_directory import GitDirectory
from .git_manager import GitManager
from .git_directory import GitDirectoryEvent
import subprocess, re, os, shutil

# ------------------------------------------------------------------------------
# Git installation.
# ------------------------------------------------------------------------------

class GitInstallation(MultiStageProcess, ABC):
    """Handles the full git directory installation lifecycle."""

    # Overwrite in subclassed
    @classmethod
    @abstractmethod
    def manager(cls) -> GitManager:
        pass

    def __init__(self, name: str, repository_url: str | None = None):
        self.alias = name
        self.repository_url = repository_url
        super().__init__(title="GIT directory installation")

    def name(self) -> str:
        return self.alias

    def setup_stages(self):
        if self.repository_url:
            self.stages.append(
                GitInstallationStepClone(
                    alias=self.alias,
                    repository_url=self.repository_url,
                    item_class=self.manager().repository()._cls,
                    multistage_process=self
                )
            )
        else:
            self.stages.append(
                GitInstallationStepInitLocal(
                    alias=self.alias,
                    item_class=self.manager().repository()._cls,
                    multistage_process=self
                )
            )
            GitInstallationStepInitLocal
        super().setup_stages()

    def complete_process(self, success: bool):
        if success:
            manager = self.__class__.manager()
            manager.add_directory(
                directory=self.directory
            )

# ------------------------------------------------------------------------------
# Installation process steps.
# ------------------------------------------------------------------------------

class GitInstallationStepClone(MultiStageProcessStage):
    def __init__(self, alias: str, repository_url: str, item_class: type[GitDirectory], multistage_process: MultiStageProcess):
        super().__init__(name="Clone GIT repository", description="Clones GIT repository", multistage_process=multistage_process)
        self.alias = alias
        self.repository_url = repository_url
        self.item_class = item_class
        self.process_started = False
    def start(self):
        super().start()
        try:
            self.directory = self.item_class(name=self.alias)
            if os.path.exists(self.directory.directory_path()):
                raise RuntimeError(f"Directory {self.directory.directory_path()} already exists")
            self.process_started = True
            progress_pattern = re.compile(r"Receiving objects:\s+(\d+)%")
            process = subprocess.Popen(
                [
                    "git",
                    "clone",
                    "--progress",
                    self.repository_url,
                    self.directory.directory_path()
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            for line in process.stdout:
                match = progress_pattern.search(line)
                if match:
                    percent = int(match.group(1))
                    self._update_progress(percent / 100)
            process.wait()
            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, process.args)
            self.directory.update_status(wait=True)
            self.directory.update_logs(wait=True)
            self.multistage_process.directory = self.directory
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during releng cloning: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        if self.multistage_process.status == MultiStageProcessState.FAILED and self.process_started:
            if os.path.exists(self.directory.directory_path()):
                shutil.rmtree(self.directory.directory_path())
        return True

class GitInstallationStepInitLocal(MultiStageProcessStage):
    def __init__(
        self,
        alias: str,
        item_class: type[GitDirectory],
        multistage_process: MultiStageProcess
    ):
        super().__init__(
            name="Initialize Local GIT Repository",
            description="Initializes an empty local GIT repository",
            multistage_process=multistage_process
        )
        self.alias = alias
        self.item_class = item_class
        self.process_started = False

    def start(self):
        super().start()
        try:
            self.directory = self.item_class(name=self.alias)
            path = self.directory.directory_path()
            if os.path.exists(path):
                raise RuntimeError(f"Directory {path} already exists")
            os.makedirs(path, exist_ok=True)
            self.process_started = True
            process = subprocess.run(
                ["git", "init", path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, process.args, output=process.stdout)
            self.directory.update_status(wait=True)
            self.directory.update_logs(wait=True)
            self.multistage_process.directory = self.directory
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during local Git init: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        if self.multistage_process.status == MultiStageProcessState.FAILED and self.process_started:
            path = self.directory.directory_path()
            if os.path.exists(path):
                shutil.rmtree(path)
        return True

