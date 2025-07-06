from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from .repository import Serializable, Repository
from typing import Self
import os
import subprocess
import re
from collections import defaultdict
from .architecture import Architecture
from typing import NamedTuple

@dataclass
class Snapshot(Serializable):
    filename: str
    date: datetime | None
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
        return self.date.strftime("%Y-%m-%d %H:%M") if self.date else "(Unknown date)"

    @property
    def short_details(self) -> str:
        return self.filename.rsplit('.', 1)[0]

    def file_path(self) -> str:
        snapshots_location = os.path.realpath(os.path.expanduser(Repository.Settings.value.snapshots_location))
        return os.path.join(snapshots_location, self.filename)

    def load_ebuilds(self) -> dict[str, dict[str, list[str]]]:
        snapshot_file_path = self.file_path()

        try:
            output = subprocess.check_output(['unsquashfs', '-l', snapshot_file_path], text=True)
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

    def load_profiles(self, arch: Architecture) -> list[PortageProfile]:
        profiles_contents = subprocess.check_output(['unsquashfs', '-cat', self.file_path(), "profiles/profiles.desc"], text=True)
        return [
            PortageProfile(parts[1], parts[2])
            for line in profiles_contents.splitlines()
            if (parts := line.split()) and parts[0] == arch.value and len(parts) >= 3
        ]

class PortageProfile(NamedTuple):
    path: str
    stability: str

