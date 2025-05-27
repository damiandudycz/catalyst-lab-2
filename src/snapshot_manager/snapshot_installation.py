from __future__ import annotations
import os, threading, shutil
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
from .helper_functions import mount_squashfs, umount_squashfs

# ------------------------------------------------------------------------------
# Installation process.
# ------------------------------------------------------------------------------

class SnapshotInstallation(MultiStageProcess):
    """Handles the full snapshot generation lifecycle."""
    def __init__(self, toolset: Toolset | None, file: GLocalFile | None, custom_filename: str | None):
        """Use either file or toolset, not both."""
        self.toolset = toolset
        self.file = file
        self.custom_filename = custom_filename # Works only with file
        super().__init__(title="Generating Portage snapshot")

    def setup_stages(self):
        if self.toolset:
            self.stages.append(SnapshotInstallationStepPrepareToolset(toolset=self.toolset, multistage_process=self))
            self.stages.append(SnapshotInstallationStepGenerateSnapshot(multistage_process=self))
        if self.file:
            self.stages.append(SnapshotInstallationStepCopyFile(file=self.file, custom_filename=self.custom_filename, multistage_process=self))
        self.stages.append(SnapshotInstallationStepSetupPermissions(multistage_process=self))
        self.stages.append(SnapshotInstallationStepAnalyze(multistage_process=self))
        super().setup_stages()

    def complete_process(self, success: bool):
        if success:
            SnapshotManager.shared().add_snapshot(self.snapshot)

    def name(self) -> str:
        return "Fetching snapshot"

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

class SnapshotInstallationStepCopyFile(SnapshotInstallationStep):
    def __init__(self, file: GLocalFile, custom_filename: str | None, multistage_process: MultiStageProcess):
        super().__init__(name="Copy snapshot file", description="Copies snapshot file to repository location", multistage_process=multistage_process)
        self.file = file
        self.custom_filename = custom_filename
    def start(self):
        super().start()
        try:
            def sanitize_filename_linux(name: str) -> str:
                return name.replace('/', '_').replace('\0', '_')
            filename = sanitize_filename_linux(self.custom_filename or self.file.get_basename())
            snapshots_location = os.path.realpath(os.path.expanduser(Repository.SETTINGS.value.snapshots_location))
            snapshot_copy_path = os.path.join(snapshots_location, filename)
            source_path = self.file.get_path()
            creation_timestamp = os.stat(source_path).st_ctime
            creation_date = datetime.fromtimestamp(creation_timestamp)
            if os.path.exists(snapshot_copy_path):
                raise ValueError("Snapshot with the same filename exists")
            total_size = os.path.getsize(source_path)
            copied_size = 0
            buffer_size = 1024 * 1024  # 1 MB
            with open(source_path, 'rb') as src, open(snapshot_copy_path, 'wb') as dst:
                while True:
                    buf = src.read(buffer_size)
                    if not buf:
                        break
                    dst.write(buf)
                    copied_size += len(buf)
                    self._update_progress(progress=copied_size / total_size)
            self.multistage_process.snapshot = Snapshot(filename=filename, date=creation_date)
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during toolset copying: {e}")
            self.complete(MultiStageProcessStageState.FAILED)

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
        # Remove file if installation failed
        if self.multistage_process.status == MultiStageProcessState.FAILED and self.multistage_process.snapshot:
            snapshots_location = os.path.realpath(os.path.expanduser(Repository.SETTINGS.value.snapshots_location))
            snapshot_real_path = os.path.join(snapshots_location, self.multistage_process.snapshot.filename)
            delete_file(file_path=snapshot_real_path, root_dir=snapshots_location)
        return True

class SnapshotInstallationStepAnalyze(SnapshotInstallationStep):
    def __init__(self, multistage_process: MultiStageProcess):
        super().__init__(name="Analyze snapshots", description="Read metadata from snapshot", multistage_process=multistage_process)
    def start(self):
        super().start()
        try:
            if self.multistage_process.snapshot is None:
                raise RuntimeError("Unknown spanshot")
            snapshots_location = os.path.realpath(os.path.expanduser(Repository.SETTINGS.value.snapshots_location))
            snapshot_real_path = os.path.join(snapshots_location, self.multistage_process.snapshot.filename)
            # Mount snapshot and read timestamp from it.
            self.multistage_process.squashfs_mount_path = mount_squashfs(squashfs_path=snapshot_real_path)
            snapshot_metadata_path = os.path.join(self.multistage_process.squashfs_mount_path, 'metadata')
            snapshot_metadata_timestamp_path = os.path.join(snapshot_metadata_path, 'timestamp.chk')
            if not os.path.isfile(snapshot_metadata_timestamp_path):
                raise RuntimeError("Timestamp metadata not found")
            def read_timestamp_from_file(snapshot_metadata_timestamp_path):
                with open(snapshot_metadata_timestamp_path, 'r', encoding='utf-8') as file:
                    timestamp_str = file.read().strip()
                timestamp = datetime.strptime(timestamp_str, "%a, %d %b %Y %H:%M:%S %z")
                return timestamp
            self.multistage_process.snapshot.date = read_timestamp_from_file(snapshot_metadata_timestamp_path=snapshot_metadata_timestamp_path)
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during snapshot generation: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        # Umount snapshot
        if hasattr(self.multistage_process, 'squashfs_mount_path'):
            umount_squashfs(self.multistage_process.squashfs_mount_path)
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

