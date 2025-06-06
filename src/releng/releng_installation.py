from .multistage_process import (
    MultiStageProcess, MultiStageProcessStage,
    MultiStageProcessState, MultiStageProcessStageState,
    MultiStageProcessEvent, MultiStageProcessStageEvent,
)
from .releng_directory import RelengDirectory
from .releng_manager import RelengManager
from .git_directory import GitDirectoryEvent
import subprocess, re, os, shutil

# ------------------------------------------------------------------------------
# Releng installation.
# ------------------------------------------------------------------------------

class RelengInstallation(MultiStageProcess):
    """Handles the full releng directory installation lifecycle."""
    def __init__(self, name: str):
        self.alias = name
        super().__init__(title="Releng directory installation")

    def name(self) -> str:
        return self.alias

    def setup_stages(self):
        self.stages.append(
            RelengInstallationStepClone(
                alias=self.alias,
                multistage_process=self
            )
        )
        super().setup_stages()

    def complete_process(self, success: bool):
        if success:
            RelengManager.shared().add_directory(
                directory=self.releng_directory
            )

# ------------------------------------------------------------------------------
# Installation process steps.
# ------------------------------------------------------------------------------

class RelengInstallationStepClone(MultiStageProcessStage):
    def __init__(self, alias: str, multistage_process: MultiStageProcess):
        super().__init__(name="Clone releng repository", description="Clones current releng repository using GIT", multistage_process=multistage_process)
        self.alias = alias
        self.process_started = False
    def start(self):
        super().start()
        try:
            self.releng_directory = RelengDirectory(name=self.alias)
            if os.path.exists(self.releng_directory.directory_path()):
                raise RuntimeError(f"Directory {self.releng_directory.directory_path()} already exists")
            self.process_started = True
            progress_pattern = re.compile(r"Receiving objects:\s+(\d+)%")
            process = subprocess.Popen(
                ["git", "clone", "--progress", "https://github.com/gentoo/releng.git", self.releng_directory.directory_path()],
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
            self.releng_directory.update_status(wait=True)
            self.releng_directory.update_logs(wait=True)
            self.multistage_process.releng_directory = self.releng_directory
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during releng cloning: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        if self.multistage_process.status == MultiStageProcessState.FAILED and self.process_started:
            if os.path.exists(self.releng_directory.directory_path()):
                shutil.rmtree(self.releng_directory.directory_path())
        return True

