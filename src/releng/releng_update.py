from __future__ import annotations
import os, threading, shutil, re, time, subprocess
from .multistage_process import (
    MultiStageProcess, MultiStageProcessStage,
    MultiStageProcessState, MultiStageProcessStageState,
    MultiStageProcessEvent, MultiStageProcessStageEvent,
)
from .releng_directory import RelengDirectory
from .releng_manager import RelengManager
from .repository import Repository
from datetime import datetime
from gi.repository import GLib, Gio

# ------------------------------------------------------------------------------
# Releng update.
# ------------------------------------------------------------------------------

class RelengUpdate(MultiStageProcess):
    """Handles the releng directory update lifecycle."""
    def __init__(self, releng_directory: RelengDirectory):
        self.releng_directory = releng_directory
        super().__init__(title="Releng directory update")

    def setup_stages(self):
        self.stages.append(RelengUpdateStepUpdate(releng_directory=self.releng_directory, multistage_process=self))
        super().setup_stages()

    def complete_process(self, success: bool):
        if success:
            # ...
            Repository.RelengDirectory.save()

# ------------------------------------------------------------------------------
# Update process steps.
# ------------------------------------------------------------------------------

class RelengUpdateStepUpdate(MultiStageProcessStage):
    def __init__(self, releng_directory: RelengDirectory, multistage_process: MultiStageProcess):
        super().__init__(name="Update releng directory", description="Fetch latest changes from git and rebase", multistage_process=multistage_process)
        self.releng_directory = releng_directory
    def start(self):
        super().start()
        repo_path = self.releng_directory.directory_path()
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
                raise RuntimeError(f"Directory {repo_path} does not exist for update")
            self.process_started = True
            run_git_command(["fetch", "--all", "--prune"])
            run_git_command(["rebase", "--autostash", "--rebase-merges", "origin/HEAD"])
            self.releng_directory.update_status(wait=True)
            self.releng_directory.update_logs(wait=True)
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during releng directory update: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        if self.multistage_process.status == MultiStageProcessState.FAILED and self.process_started:
            run_git_command(["rebase", "--abort"])
        return True

