from __future__ import annotations
import os, threading, shutil, re, time
from .multistage_process import (
    MultiStageProcess, MultiStageProcessStage,
    MultiStageProcessState, MultiStageProcessStageState,
    MultiStageProcessEvent, MultiStageProcessStageEvent,
)
from .toolset import Toolset, BindMount
from .snapshot_manager import SnapshotManager, Snapshot
from .root_function import root_function
from .repository import Repository
from .root_helper_server import ServerResponse, ServerResponseStatusCode
from datetime import datetime
from .helper_functions import mount_squashfs, umount_squashfs, create_squashfs

# ------------------------------------------------------------------------------
# Toolset update.
# ------------------------------------------------------------------------------

class ToolsetUpdate(MultiStageProcess):
    """Handles the toolset update lifecycle. Also supports changing app selection, versions and patches."""
    def __init__(self, toolset: Toolset):
        self.toolset = toolset
#        self._process_selected_apps()
        super().__init__(title="Toolset update")

    def start(self, authorization_keeper: AuthorizationKeeper | None = None):
        if not self.toolset.reserve():
            raise RuntimeError("Failed to reserve toolset")
        super().start(authorization_keeper=authorization_keeper)

    def setup_stages(self):
        self.stages.append(ToolsetUpdateStepPrepareToolset(toolset=self.toolset, multistage_process=self))
        self.stages.append(ToolsetUpdateStepRefreshEnv(toolset=self.toolset, multistage_process=self))
        self.stages.append(ToolsetUpdateStepUpdatePortage(toolset=self.toolset, multistage_process=self))
        self.stages.append(ToolsetUpdateStepUpdatePackages(toolset=self.toolset, multistage_process=self))
        self.stages.append(ToolsetUpdateStepVerify(toolset=self.toolset, multistage_process=self))
        self.stages.append(ToolsetUpdateStepStepCompress(toolset=self.toolset, multistage_process=self))
        super().setup_stages()

    def complete_process(self, success: bool):
        print(f"___ Completed: {success}")
        if success:
            self.toolset.metadata['date_updated'] = int(time.time())
            Repository.TOOLSETS.save()
        self.toolset.release()

    def _process_selected_apps(self):
        """Manage auto_select dependencies."""
        app_selections_by_app = { app_selection.app: app_selection for app_selection in self.apps_selection }
        # Mark all dependencies as selected
        for app_selection in self.apps_selection:
            print(f"{app_selection.app.name} : {app_selection.selected}")
            if app_selection.selected:
                for dep in getattr(app_selection.app, "dependencies", []):
                    if dep in app_selections_by_app:
                        app_selections_by_app[dep] = app_selections_by_app[dep]._replace(selected=True)
        # Remove not selected entries
        self.apps_selection = [sel for sel in self.apps_selection if sel.selected]
        # Sort by dependencies
        sorted_entries: list[ToolsetApplicationSelection] = []
        def process_app_selection(app_selection: ToolsetApplicationSelection):
            for dep in getattr(app_selection.app, "dependencies", []):
                process_app_selection(app_selection=app_selections_by_app[dep])
            if not app_selection in sorted_entries:
                sorted_entries.append(app_selection)
        for app_selection in self.apps_selection:
            process_app_selection(app_selection=app_selection)
        self.apps_selection = sorted_entries

# ------------------------------------------------------------------------------
# Update process steps.
# ------------------------------------------------------------------------------

class ToolsetUpdateStep(MultiStageProcessStage):
    """Base step for toolset update steps. Contains additional code for calling toolset commands."""
    """Stages that use run_command_in_toolset must have self.toolset available."""
    def start(self):
        self.server_call = None
        super().start()
    def cancel(self):
        super().cancel()
        if self.server_call:
            self.server_call.cancel()
            if self.server_call.thread:
                self.server_call.thread.join()
            self.server_call = None
    def run_command_in_toolset(self, command: str, progress_handler: Callable[[str], float | None] | None = None) -> bool:
        try:
            return_value = False
            done_event = threading.Event()
            def completion_handler(response: ServerResponse):
                nonlocal return_value
                return_value = response.code == ServerResponseStatusCode.OK
                done_event.set()
            def output_handler(output_line: str):
                print(output_line)
                progress = progress_handler(output_line)
                if progress is not None:
                    self._update_progress(progress)
            self.server_call = self.toolset.run_command(
                command=command,
                handler=output_handler if progress_handler is not None else None,
                completion_handler=completion_handler
            )
            self.server_call.thread.join()
            done_event.wait()
            self.server_call = None
            return return_value
        except Exception as e:
            print(f"Error running toolset command: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
            return False

# Steps implementations:

class ToolsetUpdateStepPrepareToolset(ToolsetUpdateStep):
    def __init__(self, toolset: Toolset, multistage_process: MultiStageProcess):
        super().__init__(name="Prepare toolset", description="Spawns toolset with write access", multistage_process=multistage_process)
        self.toolset = toolset
        self.unspawn = False
    def start(self):
        super().start()
        try:
            if self.toolset.in_use:
                raise RuntimeError("Toolset is currently in use")
            def toolset_has_required_binding() -> bool:
                if not self.toolset.spawned:
                    return False
                if self.toolset.additional_bindings:
                    # There should be no additional bindings set
                    return False
                return True
            if self.toolset.spawned and (not toolset_has_required_binding() or not self.toolset.store_changes):
                # Toolset needs to be respawned to get correct bindings and write access
                self.toolset.unspawn()
            if not self.toolset.spawned:
                self.toolset.spawn(store_changes=True)
                self.unspawn = True
            else:
                self.unspawn = False
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during toolset preparation: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        if self.unspawn:
            self.toolset.unspawn(rebuild_squashfs_if_needed=False) # New squashFS is build as another step in update process.
        return True

class ToolsetUpdateStepRefreshEnv(ToolsetUpdateStep):
    def __init__(self, toolset: Toolset, multistage_process: MultiStageProcess):
        super().__init__(name="Refresh environment", description="Refreshes binpkg signing keys", multistage_process=multistage_process)
        self.toolset = toolset
    def start(self):
        super().start()
        try:
            # Prepare environment
            commands = [
                "env-update && source /etc/profile",
                "getuto"
            ]
            for i, command in enumerate(commands):
                if self._cancel_event.is_set():
                    return
                result = self.run_command_in_toolset(command=command)
                self._update_progress(i / len(commands))
                if not result:
                    raise RuntimeError(f"Command {command} failed")
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during keys refresh: {e}")
            self.complete(MultiStageProcessStageState.FAILED)

class ToolsetUpdateStepUpdatePortage(ToolsetUpdateStep):
    def __init__(self, toolset: Toolset, multistage_process: MultiStageProcess):
        super().__init__(name="Synchronize portage", description="Synchronizes portage tree", multistage_process=multistage_process)
        self.toolset = toolset
    def start(self):
        super().start()
        try:
            def progress_handler(output_line: str) -> float or None:
                pattern = (
                    r"\s*"                        # optional leading spaces
                    r"\d+[KMGTP]?"                # downloaded size (e.g., 45500K, 4.47T)
                    r"\s+(?:\.{1,10}\s*)+"        # progress dots (at least one group)
                    r"(\d{1,3})%"                 # percentage (captured)
                    r"\s+\d+(\.\d+)?[KMGTP]?"     # speed (like 4.47T, 14.5M)
                    r"(?:[= ]\d+(\.\d+)?s?)?"     # optional time (e.g., =2.2s, 0s)"
                )
                match = re.match(pattern, output_line)
                if match:
                    return int(match.group(1)) / 100.0
            result = self.run_command_in_toolset(command="emerge-webrsync", progress_handler=progress_handler)
            self.complete(MultiStageProcessStageState.COMPLETED if result else MultiStageProcessStageState.FAILED)
        except Exception as e:
            print(f"Error synchronizing Portage: {e}")
            self.complete(MultiStageProcessStageState.FAILED)

class ToolsetUpdateStepUpdatePackages(ToolsetUpdateStep):
    def __init__(self, toolset: Toolset, multistage_process: MultiStageProcess):
        super().__init__(name="Update packages", description="Updates installed packages", multistage_process=multistage_process)
        self.toolset = toolset
    def start(self):
        super().start()
        try:
            def progress_handler(output_line: str) -> float or None:
                pattern = r"^>>> Completed \((\d+) of (\d+)\)"
                match = re.match(pattern, output_line)
                if match:
                    n, m = map(int, match.groups())
                    return n / m
            allow_binpkgs = self.toolset.metadata.get('allow_binpkgs', False)
            flags = "--getbinpkg --changed-use --update --deep --with-bdeps=y" if allow_binpkgs else "--changed-use --update --deep --with-bdeps=y"
            result = self.run_command_in_toolset(command=f"emerge {flags} @system @world @live-rebuild", progress_handler=progress_handler)
            self.complete(MultiStageProcessStageState.COMPLETED if result else MultiStageProcessStageState.FAILED)
        except Exception as e:
            print(f"Error updating packages: {e}")
            self.complete(MultiStageProcessStageState.FAILED)

class ToolsetUpdateStepVerify(ToolsetUpdateStep):
    def __init__(self, toolset: Toolset, multistage_process: MultiStageProcess):
        super().__init__(name="Analyze toolset", description="Collects information about toolset", multistage_process=multistage_process)
        self.toolset = toolset
    def start(self):
        super().start()
        try:
            analysis_result = self.toolset.analyze()
            self.complete(MultiStageProcessStageState.COMPLETED if analysis_result else MultiStageProcessStageState.FAILED)
        except Exception as e:
            print(f"Error during toolset verification: {e}")
            self.complete(MultiStageProcessStageState.FAILED)

class ToolsetUpdateStepStepCompress(ToolsetUpdateStep):
    def __init__(self, toolset: Toolset, multistage_process: MultiStageProcess):
        super().__init__(name="Compress", description="Compresses toolset into .squashfs file", multistage_process=multistage_process)
        self.toolset = toolset
    def start(self):
        super().start()
        try:
            toolset_tmp_squashfs_path = self.toolset.squashfs_file + "_tmp"
            self.squashfs_process = create_squashfs(source_directory=self.toolset.toolset_root(), output_file=toolset_tmp_squashfs_path)
            for line in self.squashfs_process.stdout:
                line = line.strip()
                if line.isdigit():
                    percent = int(line)
                    self._update_progress(percent / 100.0)
            self.squashfs_process.wait()
            self.squashfs_process = None
            shutil.move(toolset_tmp_squashfs_path, self.toolset.squashfs_file)
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during toolset compression: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
    def cancel(self):
        super().cancel()
        proc = self.squashfs_process
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=3)
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        self.squashfs_process = None

