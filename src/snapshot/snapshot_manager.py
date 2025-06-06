from dataclasses import dataclass, field
from datetime import datetime
from .repository import Serializable, Repository
from typing import Self
from .toolset import Toolset, BindMount
from .root_helper_client import RootHelperClient, ServerResponse, ServerResponseStatusCode, AuthorizationKeeper
from .root_function import root_function
import os
import subprocess
import re
from collections import defaultdict
from .snapshot import Snapshot

class SnapshotManager:
    _instance = None

    # Note: To get spanshots list use Repository.Snapshot.value

    @classmethod
    def shared(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def refresh_snapshots(self):
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
        Repository.Snapshot.value = [s for s in Repository.Snapshot.value if s.filename != snapshot.filename]
        Repository.Snapshot.value.append(snapshot)

    def remove_snapshot(self, snapshot: Snapshot):
        if os.path.isfile(snapshot.file_path()):
            os.remove(snapshot.file_path())
        Repository.Snapshot.value.remove(snapshot)

