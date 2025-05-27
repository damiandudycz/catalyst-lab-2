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

    # Note: To get spanshots list use Repository.SNAPSHOTS.value

    @classmethod
    def shared(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def refresh_snapshots(self):
        snapshots = Repository.SNAPSHOTS.value
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
        existing_filenames = {snapshot.filename for snapshot in snapshots}
        missing_files = found_filenames - existing_filenames
        for filename in missing_files:
            full_path = os.path.join(snapshots_location, filename)
            stat_info = os.stat(full_path)
            # TODO: Load date from squashfs instead
            creation_time = datetime.fromtimestamp(stat_info.st_ctime)
            self.add_snapshot(Snapshot(filename=filename, date=creation_time))
        # --- Step 3: Remove records for deleted snapshot files ---
        deleted_snapshots = [snapshot for snapshot in snapshots if snapshot.filename not in found_filenames]
        for snapshot in deleted_snapshots:
            self.remove_snapshot(snapshot)

    def add_snapshot(self, snapshot: Snapshot):
        # Remove existing snapshot with the same filename
        Repository.SNAPSHOTS.value = [s for s in Repository.SNAPSHOTS.value if s.filename != snapshot.filename]
        Repository.SNAPSHOTS.value.append(snapshot)

    def remove_snapshot(self, snapshot: Snapshot):
        Repository.SNAPSHOTS.value.remove(snapshot)

