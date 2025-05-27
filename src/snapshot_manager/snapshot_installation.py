from __future__ import annotations
import os, threading
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

# ------------------------------------------------------------------------------
# Installation process.
# ------------------------------------------------------------------------------

class SnapshotInstallation(MultiStageProcess):
    """Handles the full snapshot generation lifecycle."""
    def __init__(self, toolset: Toolset):
        self.toolset = toolset
        super().__init__(title="Generating Portage snapshot")

    def setup_stages(self):
        self.stages.append(SnapshotInstallationStepPrepareToolset(toolset=self.toolset, multistage_process=self))
        self.stages.append(SnapshotInstallationStepGenerateSnapshot(multistage_process=self))
        self.stages.append(SnapshotInstallationStepSetupPermissions(multistage_process=self))
        super().setup_stages()

    def complete_process(self, success: bool):
        return
        if success:
            SnapshotManager.shared().add_snapshot(self.snapshot)

# ------------------------------------------------------------------------------
# Installation process steps.
# ------------------------------------------------------------------------------

class SnapshotInstallationStep(MultiStageProcessStage):
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
            self.server_call = self.multistage_process.toolset.run_command(
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

class SnapshotInstallationStepPrepareToolset(SnapshotInstallationStep):
    def __init__(self, toolset: Toolset, multistage_process: MultiStageProcess):
        super().__init__(name="Prepare toolset", description="Spawns toolset with required bindings", multistage_process=multistage_process)
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
                required_bindings = self.required_bindings()
                current_bindings = self.toolset.additional_bindings or []
                for required in required_bindings:
                    print(f"Checking if toolset contains: {required.mount_path}")
                    found = any(
                        existing.mount_path == required.mount_path and
                        existing.host_path == required.host_path and
                        existing.toolset_path == required.toolset_path
                        for existing in current_bindings
                    )
                    if not found:
                        print(f"Missing required binding: {required}")
                        return False
                return True
            if self.toolset.spawned and not toolset_has_required_binding():
                # Toolset needs to be respawned to get correct bindings
                self.toolset.unspawn()
            if not self.toolset.spawned:
                self.toolset.spawn(additional_bindings=self.required_bindings())
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
            self.toolset.unspawn()
        return True
    def required_bindings(self) -> [BindMount]:
        return [
            BindMount(
                mount_path="/var/tmp/catalyst/snapshots",
                host_path=Repository.SETTINGS.value.snapshots_location,
                store_changes=True,
                create_if_missing=True
            )
        ]

class SnapshotInstallationStepGenerateSnapshot(SnapshotInstallationStep):
    def __init__(self, multistage_process: MultiStageProcess):
        super().__init__(name="Generate snapshot", description="Fetches latest portage snapshot tree", multistage_process=multistage_process)
    def start(self):
        super().start()
        try:
            snapshot_path: str | None = None
            def catalyst_snapshot_handler(line: str) -> float | None:
                nonlocal snapshot_path
                prefix = "NOTICE:catalyst:Wrote snapshot to "
                if line.startswith(prefix):
                    snapshot_path = line[len(prefix):].strip()
                return None
            if not self.run_command_in_toolset(command="catalyst -s stable", progress_handler=catalyst_snapshot_handler):
                raise RuntimeError("Catalyst -s stable failed")
            if not snapshot_path:
                raise RuntimeError("Didn't find generated snapshot")
            filename = os.path.basename(snapshot_path)
            self.multistage_process.snapshot = Snapshot(filename=filename, date=datetime.now())
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during snapshot generation: {e}")
            self.complete(MultiStageProcessStageState.FAILED)

class SnapshotInstallationStepSetupPermissions(SnapshotInstallationStep):
    def __init__(self, multistage_process: MultiStageProcess):
        super().__init__(name="Setup permissions", description="Sets generated snapshot file permissions", multistage_process=multistage_process)
    def start(self):
        super().start()
        try:
            if self.multistage_process.snapshot is None:
                raise RuntimeError("Unknown spanshot")
            snapshots_location = os.path.realpath(os.path.expanduser(Repository.SETTINGS.value.snapshots_location))
            snapshot_real_path = os.path.join(snapshots_location, self.multistage_process.snapshot.filename)
            base_name, _ = os.path.splitext(self.multistage_process.snapshot.filename)
            lock_filename = base_name + ".lock"
            lock_file_path = os.path.join(snapshots_location, lock_filename)
            delete_file(file_path=lock_file_path, root_dir=snapshots_location)
            unlock_file_access(snapshot_real_path)
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during snapshot generation: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        return True

# ------------------------------------------------------------------------------
# Helper functions.
# ------------------------------------------------------------------------------

@root_function
def unlock_file_access(path: str):
    os.chmod(path, 0o777)

@root_function
def delete_file(file_path: str, root_dir: str):
    if not root_dir.strip():
        print("Root directory path cannot be empty")
        return
    file_path = os.path.abspath(file_path)
    root_dir = os.path.abspath(root_dir)
    # Check if file_path is inside root_dir
    if not file_path.startswith(root_dir + os.sep):
        print(f"File '{file_path}' is not inside root directory '{root_dir}'")
        return
    # Check if it's a regular file
    if not os.path.isfile(file_path):
        print(f"'{file_path}' is not a regular file")
        return
    # Delete the file
    os.remove(file_path)

