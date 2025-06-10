from typing import final
from datetime import datetime
from .repository import Repository
import os, subprocess
from .snapshot import Snapshot

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
                output = subprocess.check_output(['/app/bin/unsquashfs', '-cat', full_path, "metadata/timestamp.chk"], text=True)
                timestamp = datetime.strptime(output.strip(), "%a, %d %b %Y %H:%M:%S %z")
                self.add_snapshot(Snapshot(filename=filename, date=timestamp))
            except subprocess.CalledProcessError as e:
                print(f"Error reading {full_path}: {e}")
        # --- Step 3: Remove records for deleted snapshot files ---
        deleted_snapshots = [snapshot for snapshot in snapshots if snapshot.filename not in found_filenames]
        for snapshot in deleted_snapshots:
            self.remove_snapshot(snapshot)

    def add_snapshot(self, snapshot: Snapshot):
        # Remove existing snapshot with the same filename
        Repository.Snapshot.value = [s for s in Repository.Snapshot.value if s.filename != snapshot.filename]
        Repository.Snapshot.value.append(snapshot)

    def remove_snapshot(self, snapshot: Snapshot):
        if os.path.isfile(snapshot.file_path()):
            os.remove(snapshot.file_path())
        Repository.Snapshot.value.remove(snapshot)

