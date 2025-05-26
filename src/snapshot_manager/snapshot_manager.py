from dataclasses import dataclass, field
from datetime import datetime
from .repository import Serializable, Repository
from typing import Self
from .toolset import Toolset, BindMount
from .root_helper_client import RootHelperClient, ServerResponse, ServerResponseStatusCode, AuthorizationKeeper
from .root_function import root_function
import os

@dataclass
class Snapshot(Serializable):
    filename: str
    date: datetime
    def serialize(self) -> dict:
        return {
            "filename": self.filename,
            "date": self.date.isoformat() if self.date else None
        }
    @classmethod
    def init_from(cls, data: dict) -> Self:
        return cls(
            filename=data["filename"],
            date=datetime.fromisoformat(data["date"]) if data.get("date") else None
        )

class SnapshotManager:
    _instance = None

    @classmethod
    def shared(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.snapshots = Repository.SNAPSHOTS.value
        self.refresh_snapshots()

    def refresh_snapshots(self):
        # Detect missing snapshots and add them to repository without date
        snapshots_location = os.path.realpath(os.path.expanduser(Repository.SETTINGS.value.snapshots_location))
        # --- Step 1: Scan directory for existing .sqfs files ---
        if not os.path.isdir(snapshots_location):
            os.makedirs(snapshots_location, exist_ok=True)
        found_filenames = {
            f for f in os.listdir(snapshots_location)
            if f.endswith(".sqfs") and os.path.isfile(os.path.join(snapshots_location, f))
        }
        # --- Step 2: Check for new snapshots not in repository ---
        existing_filenames = {snapshot.filename for snapshot in self.snapshots}
        missing_files = found_filenames - existing_filenames
        for filename in missing_files:
            full_path = os.path.join(snapshots_location, filename)
            stat_info = os.stat(full_path)
            creation_time = datetime.fromtimestamp(stat_info.st_ctime)
            self.add_snapshot(Snapshot(filename=filename, date=creation_time))
        # --- Step 3: Remove records for deleted snapshot files ---
        deleted_snapshots = [snapshot for snapshot in self.snapshots if snapshot.filename not in found_filenames]
        for snapshot in deleted_snapshots:
            self.remove_snapshot(snapshot)

    def add_snapshot(self, snapshot: Snapshot):
        # Remove existing snapshot with the same filename
        Repository.SNAPSHOTS.value = [
            s for s in Repository.SNAPSHOTS.value if s.filename != snapshot.filename
        ]
        Repository.SNAPSHOTS.value.append(snapshot)
        self.snapshots = Repository.SNAPSHOTS.value

    def remove_snapshot(self, snapshot: Snapshot):
        Repository.SNAPSHOTS.value.remove(snapshot)
        self.snapshots = Repository.SNAPSHOTS.value

    def required_bindings(self) -> [BindMount]:
        return [
            BindMount(
                mount_path="/var/tmp/catalyst/snapshots",
                host_path=Repository.SETTINGS.value.snapshots_location,
                store_changes=True,
                create_if_missing=True
            )
        ]

    def generate_new_snapshot(self, toolset: Toolset, spawn_toolset_if_needed: bool = True):
        if not toolset.spawned and not spawn_toolset_if_needed:
            raise RuntimeError("Toolset is not spawned")
        if toolset.in_use:
            raise RuntimeError("Toolset is currently in use")

        def toolset_has_required_binding() -> bool:
            if not toolset.spawned:
                return False
            required_bindings = self.required_bindings()
            current_bindings = toolset.additional_bindings or []
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

        if toolset.spawned and not toolset_has_required_binding() and not spawn_toolset_if_needed:
            raise RuntimeError("Snapshot binding not set correctly")

        snapshots_location = os.path.realpath(os.path.expanduser(Repository.SETTINGS.value.snapshots_location))
        def worker(authorization_keeper: AuthorizationKeeper):
            if not authorization_keeper:
                return
            try:
                if toolset.spawned and not toolset_has_required_binding():
                    """Toolset needs to be respawned to get correct bindings"""
                    toolset.unspawn()
                if not toolset.spawned:
                    unspawn = True
                    toolset.spawn(additional_bindings=self.required_bindings())
                else:
                    unspawn = False
                snapshot_path: str | None = None
                def catalyst_snapshot_handler(line: str):
                    nonlocal snapshot_path
                    prefix = "NOTICE:catalyst:Wrote snapshot to "
                    if line.startswith(prefix):
                        snapshot_path = line[len(prefix):].strip()
                def catalyst_snapshot_completion_handler(result: ServerResponse):
                    nonlocal snapshot_path
                    print(f"[[ {result.code.name}, {snapshot_path} ]]")
                    if result.code == ServerResponseStatusCode.OK and snapshot_path is not None:
                        filename = os.path.basename(snapshot_path)
                        snapshot_real_path = os.path.join(snapshots_location, filename)
                        base_name, _ = os.path.splitext(filename)
                        lock_filename = base_name + ".lock"
                        lock_file_path = os.path.join(snapshots_location, lock_filename)
                        delete_file(file_path=lock_file_path, root_dir=snapshots_location)
                        unlock_file_access(snapshot_real_path)
                        snapshot = Snapshot(filename=filename, date=datetime.now())
                        self.add_snapshot(snapshot=snapshot)
                        if unspawn:
                            toolset.unspawn()
                toolset.run_command(command="catalyst -s stable", handler=catalyst_snapshot_handler, completion_handler=catalyst_snapshot_completion_handler)
            except Exception as e:
                print(e)
        RootHelperClient.shared().authorize_and_run(callback=worker)

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

