from __future__ import annotations
import os, threading, shutil, re, time, subprocess
from .multistage_process import (
    MultiStageProcess, MultiStageProcessStage,
    MultiStageProcessState, MultiStageProcessStageState,
    MultiStageProcessEvent, MultiStageProcessStageEvent,
)
from .git_directory import GitDirectory
from .git_manager import GitManager
from .repository import Repository
from datetime import datetime
from gi.repository import GLib, Gio
from abc import ABC, abstractmethod

# ------------------------------------------------------------------------------
# Git update.
# ------------------------------------------------------------------------------

class GitUpdate(MultiStageProcess):
    """Handles the GIT directory update lifecycle."""

    # Overwrite in subclassed
    @classmethod
    @abstractmethod
    def manager(cls) -> GitManager:
        pass

    def __init__(self, directory: GitDirectory):
        self.directory = directory
        super().__init__(title="GIT directory update")

    def setup_stages(self):
        self.stages.append(
            GitUpdateStepUpdate(
                directory=self.directory,
                multistage_process=self
            )
        )
        super().setup_stages()

    def complete_process(self, success: bool):
        if success:
            self.__class__.manager().repository().save()

# ------------------------------------------------------------------------------
# Update process steps.
# ------------------------------------------------------------------------------

class GitUpdateStepUpdate(MultiStageProcessStage):
    def __init__(
        self,
        directory: GitDirectory,
        multistage_process: MultiStageProcess
    ):
        super().__init__(
            name="Update GIT directory",
            description="Fetch latest changes from git and rebase",
            multistage_process=multistage_process
        )
        self.directory = directory
    def start(self):
        super().start()
        repo_path = self.directory.directory_path()
        def run_git_command(args):
            result = subprocess.run(
                ["git"] + args,
                cwd=repo_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=True
            )
            return result.stdout.strip()
        try:
            if not os.path.exists(repo_path):
                raise RuntimeError(
                    f"Directory {repo_path} does not exist for update"
                )
            self.process_started = True
            run_git_command(
                ["fetch", "--all", "--prune"]
            )
            run_git_command(
                ["rebase", "--autostash", "--rebase-merges", "origin/HEAD"]
            )
            self.directory.update_status(wait=True)
            self.directory.update_logs(wait=True)
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during GIT directory update: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        if self.multistage_process.status == MultiStageProcessState.FAILED and self.process_started:
            run_git_command(["rebase", "--abort"])
        return True

