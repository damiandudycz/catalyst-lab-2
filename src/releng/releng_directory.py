from __future__ import annotations
from dataclasses import dataclass, field
from .repository import Serializable, Repository
from typing import final, ClassVar, Dict, Any
from enum import Enum, auto
from .event_bus import EventBus
import os, threading, subprocess

class RelengDirectoryEvent(Enum):
    STATUS_CHANGED = auto()

class RelengDirectoryStatus(Enum):
    # Git status
    UNKNOWN = auto()
    UNCHANGED = auto()
    CHANGED = auto()

@final
class RelengDirectory(Serializable):

    def __init__(self, name: str):
        self.name = name
        self.status: RelengDirectoryStatus = RelengDirectoryStatus.UNKNOWN
        self.event_bus = EventBus[RelengDirectoryEvent]()

    def serialize(self) -> dict:
        return {
            "name": self.name
        }
    @classmethod
    def init_from(cls, data: dict) -> Self:
        return cls(
            name=data["name"]
        )

    def update_status(self):
        def worker():
            directory = self.directory_path()
            print(f"Checking {directory}")
            if not os.path.isdir(directory) or not os.path.isdir(os.path.join(directory, ".git")):
                print("No .git")
                self.status = RelengDirectoryStatus.UNKNOWN
                self.event_bus.emit(RelengDirectoryEvent.STATUS_CHANGED, self.status)
                return
            try:
                process = subprocess.Popen(
                    ["git", "status", "--porcelain"],
                    cwd=directory,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True
                )
                stdout, _ = process.communicate()
                if process.returncode != 0:
                    print(f"Status: {process.returncode}")
                    self.status = RelengDirectoryStatus.UNKNOWN
                else:
                    print(f"OUT: {stdout}")
                    self.status = RelengDirectoryStatus.CHANGED if stdout.strip() else RelengDirectoryStatus.UNCHANGED
            except Exception as e:
                print(f"EX: {e}")
                self.status = RelengDirectoryStatus.UNKNOWN
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
