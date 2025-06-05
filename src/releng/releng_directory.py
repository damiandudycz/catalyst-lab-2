from __future__ import annotations
from dataclasses import dataclass, field
from .repository import Serializable, Repository
from typing import final, ClassVar, Dict, Any
from enum import Enum, auto
from .event_bus import EventBus
import os, threading, subprocess
from datetime import datetime

class RelengDirectoryEvent(Enum):
    STATUS_CHANGED = auto()

class RelengDirectoryStatus(Enum):
    # Git status
    UNKNOWN = auto()
    UNCHANGED = auto()
    CHANGED = auto()

@final
class RelengDirectory(Serializable):

    def __init__(self, name: str, branch_name: str | None = None, last_commit_date: datetime | None = None):
        self.name = name
        self.status: RelengDirectoryStatus = RelengDirectoryStatus.UNKNOWN
        self.last_commit_date = last_commit_date
        self.branch_name = branch_name
        self.event_bus = EventBus[RelengDirectoryEvent]()

    def serialize(self) -> dict:
        return {
            "name": self.name,
            "last_commit_date": self.last_commit_date.isoformat() if self.last_commit_date else None,
            "branch_name": self.branch_name,
        }
    @classmethod
    def init_from(cls, data: dict) -> Self:
        return cls(
            name=data["name"],
            last_commit_date=(
                datetime.fromisoformat(data["last_commit_date"])
                if data.get("last_commit_date") else None
            ),
            branch_name=data.get("branch_name"),
        )

    def update_status(self):
        def worker():
            directory = self.directory_path()
            if not os.path.isdir(directory) or not os.path.isdir(os.path.join(directory, ".git")):
                self.status = RelengDirectoryStatus.UNKNOWN
                self.last_commit_date = None
                self.branch_name = None
                self.event_bus.emit(RelengDirectoryEvent.STATUS_CHANGED, self.status)
                return
            try:
                # Get git status porcelain
                process = subprocess.Popen(
                    ["git", "status", "--porcelain"],
                    cwd=directory,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True
                )
                stdout, _ = process.communicate()
                if process.returncode != 0:
                    self.status = RelengDirectoryStatus.UNKNOWN
                else:
                    self.status = RelengDirectoryStatus.CHANGED if stdout.strip() else RelengDirectoryStatus.UNCHANGED
                # Get last commit date (ISO 8601)
                process_date = subprocess.Popen(
                    ["git", "log", "-1", "--format=%cI"],
                    cwd=directory,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True
                )
                last_commit_date, _ = process_date.communicate()
                self.last_commit_date = datetime.fromisoformat(last_commit_date.strip())
                # Get current branch name
                process_branch = subprocess.Popen(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=directory,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True
                )
                branch_name, _ = process_branch.communicate()
                self.branch_name = branch_name.strip() if process_branch.returncode == 0 else None
            except Exception as e:
                print(f"EX: {e}")
                self.status = RelengDirectoryStatus.UNKNOWN
                self.last_commit_date = None
                self.branch_name = None
            self.event_bus.emit(RelengDirectoryEvent.STATUS_CHANGED, self.status)
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    @staticmethod
    def directory_path_for_name(name: str) -> str:
        releng_location = os.path.realpath(os.path.expanduser(Repository.SETTINGS.value.releng_location))
        return os.path.join(releng_location, RelengDirectory.sanitized_name_for_name(name))

    def directory_path(self) -> str:
        return RelengDirectory.directory_path_for_name(self.name)

    @staticmethod
    def sanitized_name_for_name(name: str) -> str:
        def sanitize_filename_linux(name: str) -> str:
                return name.replace('/', '_').replace('\0', '_')
        return sanitize_filename_linux(name=name)

    def sanitized_name(self) -> str:
        return RelengDirectory.sanitized_name_for_name(self.name)

    @staticmethod
    def create_directory(name: str) -> Releng:
        """Create a Releng directory with given name."""
        return RelengDirectory(name=name)
