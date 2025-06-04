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

    def file_path(self) -> str:
        snapshots_location = os.path.realpath(os.path.expanduser(Repository.SETTINGS.value.snapshots_location))
        return os.path.join(snapshots_location, self.filename)

    def load_ebuilds(self) -> dict[str, dict[str, list[str]]]:
        snapshot_file_path = self.file_path()

        try:
            output = subprocess.check_output(['/app/bin/unsquashfs', '-l', snapshot_file_path], text=True)
        except subprocess.CalledProcessError as e:
            print(f"Error reading {snapshot_file_path}: {e}")
            return {}

        nested_dict = defaultdict(lambda: defaultdict(list))

        for line in output.splitlines():
            line = line.strip()
            if not line.startswith('squashfs-root/'):
                continue

            path = line[len('squashfs-root/'):]
            if not path.endswith('.ebuild'):
                continue

            parts = path.split('/')
            if len(parts) < 3:
                continue  # Skip malformed paths

            category, package, filename = parts[:3]

            # Match version from filename: package-version.ebuild
            match = re.match(rf"^{re.escape(package)}-(.+)\.ebuild$", filename)
            if match:
                version = match.group(1)
                nested_dict[category][package].append(version)

        # Convert defaultdicts to normal dicts
        return {
            category: dict(sorted(packages.items()))
            for category, packages in sorted(nested_dict.items())
        }


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

