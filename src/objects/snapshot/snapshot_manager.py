from typing import final
from datetime import datetime
from .repository import Repository
import os, subprocess
from .snapshot import Snapshot
from .helper_functions import parse_strict_rfc_datetime

def sorted_snapshots(snapshots: list[Snapshot]) -> list[Snapshot]:
    """Newest snapshots first, snapshots without date last."""
    return sorted(snapshots, key=lambda snapshot: snapshot.date.timestamp() if snapshot.date else 0, reverse=True)

@final
class SnapshotManager:
    _instance = None

    # Note: To get spanshots list use Repository.Snapshot.value

    @classmethod
    def shared(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def refresh(self):
        snapshots = Repository.Snapshot.value
        # Detect missing snapshots and add them to repository without date
        snapshots_location = os.path.realpath(os.path.expanduser(Repository.Settings.value.snapshots_location))
        # --- Step 1: Scan directory for existing .sqfs files ---
        if not os.path.isdir(snapshots_location):
            os.makedirs(snapshots_location, exist_ok=True)
        found_filenames = {
            f for f in os.listdir(snapshots_location)
            if f.endswith(".sqfs") and os.path.isfile(os.path.join(snapshots_location, f))
        }
        # --- Step 2: Check for new snapshots not in repository ---
        existing_filenames = {snapshot.filename for snapshot in snapshots}
        missing_files = found_filenames - existing_filenames
        for filename in missing_files:
            full_path = os.path.join(snapshots_location, filename)
            try:
                output = subprocess.check_output(['unsquashfs', '-cat', full_path, "metadata/timestamp.chk"], text=True)
                try:
                    timestamp = parse_strict_rfc_datetime(output.strip())
                except Exception as e:
                    print(e)
                    timestamp = None
                self.add_snapshot(Snapshot(filename=filename, date=timestamp))
            except Exception as e:
                print(f"Error reading {full_path}: {e}")
        # --- Step 3: Remove records for deleted snapshot files ---
        deleted_snapshots = [snapshot for snapshot in snapshots if snapshot.filename not in found_filenames]
        for snapshot in deleted_snapshots:
            self.remove_snapshot(snapshot)
        # --- Step 4: Keep newest snapshots first ---
        ordered = sorted_snapshots(Repository.Snapshot.value)
        if ordered != Repository.Snapshot.value:
            Repository.Snapshot.value = ordered

    def add_snapshot(self, snapshot: Snapshot):
        # Remove existing snapshot with the same filename
        Repository.Snapshot.value = sorted_snapshots(
            [s for s in Repository.Snapshot.value if s.filename != snapshot.filename] + [snapshot]
        )

    def remove_snapshot(self, snapshot: Snapshot):
        from .rootless import remove_extracted_squashfs, MachineExecutor
        remove_extracted_squashfs(snapshot.file_path(), kind="snapshots")
        # Snapshots are extracted in machines that built with them, running ones are cleaned too.
        for machine in Repository.BuildMachine.value:
            if machine.is_running:
                remove_extracted_squashfs(snapshot.file_path(), kind="snapshots", executor=MachineExecutor(machine))
        if os.path.isfile(snapshot.file_path()):
            os.remove(snapshot.file_path())
        Repository.Snapshot.value.remove(snapshot)

