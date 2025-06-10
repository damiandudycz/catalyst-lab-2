from dataclasses import dataclass
from datetime import datetime
from .repository import Serializable, Repository
from typing import Self
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

    @property
    def name(self) -> str:
        return self.date.strftime("%Y-%m-%d %H:%M")

    @property
    def short_details(self) -> str:
        return self.filename.rsplit('.', 1)[0]

    def file_path(self) -> str:
        snapshots_location = os.path.realpath(os.path.expanduser(Repository.Settings.value.snapshots_location))
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

