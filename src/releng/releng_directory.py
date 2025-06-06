from __future__ import annotations
from dataclasses import dataclass, field
from .repository import Serializable, Repository
from typing import final, ClassVar, Dict, Any
from enum import Enum, auto
from .event_bus import EventBus
import os, threading, subprocess, uuid
from datetime import datetime

class RelengDirectoryEvent(Enum):
    STATUS_CHANGED = auto()
    LOGS_CHANGED = auto()

class RelengDirectoryStatus(Enum):
    # Git status
    UNKNOWN = auto()
    UNCHANGED = auto()
    CHANGED = auto()

@final
class RelengDirectory(Serializable):

    def __init__(self, name: str, uuid: uuid.UUID | None = None, branch_name: str | None = None, last_commit_date: datetime | None = None, has_remote_changes: bool = False):
        self.name = name
        self.uuid = uuid or uuid.uuid4()
        self.status: RelengDirectoryStatus = RelengDirectoryStatus.UNKNOWN
        self.last_commit_date = last_commit_date
        self.branch_name = branch_name
        self.has_remote_changes = has_remote_changes
        self.logs: list[dict] = []
        self.event_bus = EventBus[RelengDirectoryEvent]()

    def serialize(self) -> dict:
        return {
            "name": self.name,
            "uuid": str(self.uuid),
            "last_commit_date": self.last_commit_date.isoformat() if self.last_commit_date else None,
            "branch_name": self.branch_name,
            "has_remote_changes": self.has_remote_changes
        }
    @classmethod
    def init_from(cls, data: dict) -> Self:
        try:
            name = data["name"]
            uuid_value = uuid.UUID(data["uuid"])
            last_commit_date = (
                datetime.fromisoformat(data["last_commit_date"])
                if data.get("last_commit_date") else None
            )
            branch_name = data.get("branch_name")
            has_remote_changes = data.get("has_remote_changes")
        except KeyError:
            raise ValueError(f"Failed to parse {data}")
        return cls(
            name=name,
            uuid=uuid_value,
            last_commit_date=last_commit_date,
            branch_name=branch_name,
            has_remote_changes=has_remote_changes
        )

    def update_status(self, wait: bool = False):
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
                # Check if there are remote changes
                try:
                    process_fetch = subprocess.Popen(
                        ["git", "fetch"],
                        cwd=directory,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    process_fetch.wait()
                    if process_fetch.returncode != 0:
                        raise RuntimeError("git fetch failed")
                    process_diff = subprocess.Popen(
                        ["git", "rev-list", "--count", "--left-only", "@{u}...HEAD"],
                        cwd=directory,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    output, error = process_diff.communicate()
                    if process_diff.returncode == 0:
                        behind_count = int(output.strip())
                        self.has_remote_changes = behind_count > 0
                    else:
                        print(f"Warning: Failed to compare with upstream: {error.strip()}")
                        self.has_remote_changes = False
                except Exception as e:
                    print(f"Warning: Failed to check for updates: {e}")
                    self.has_remote_changes = False
            except Exception as e:
                print(f"STATUS EXCEPTION: {e}")
                self.status = RelengDirectoryStatus.UNKNOWN
                self.last_commit_date = None
                self.branch_name = None
            self.event_bus.emit(RelengDirectoryEvent.STATUS_CHANGED, self.status)
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        if wait:
            thread.join()

    def update_logs(self, wait: bool = False):
        def worker():
            directory = self.directory_path()
            self.logs = []
            if not os.path.isdir(directory) or not os.path.isdir(os.path.join(directory, ".git")):
                self.event_bus.emit(RelengDirectoryEvent.LOGS_CHANGED, self.logs)
                return
            try:
                # Use a custom format to make parsing easier
                log_format = "%H%x1f%an%x1f%aI%x1f%s"  # hash␟author␟ISO date␟subject
                process = subprocess.Popen(
                    ["git", "log", f"--format={log_format}"],
                    cwd=directory,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True
                )
                stdout, _ = process.communicate()
                for line in stdout.strip().splitlines():
                    parts = line.strip().split('\x1f')
                    if len(parts) == 4:
                        commit_hash, author, date_str, message = parts
                        try:
                            date = datetime.fromisoformat(date_str)
                        except ValueError:
                            date = None
                        self.logs.append({
                            "hash": commit_hash,
                            "author": author,
                            "date": date,
                            "message": message,
                        })
            except Exception as e:
                print(f"LOG EXCEPTION: {e}")
                self.logs = []
            self.event_bus.emit(RelengDirectoryEvent.LOGS_CHANGED, self.logs)
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        if wait:
            thread.join()

    def discard_changes(self, wait: bool = False):
        def worker():
            try:
                subprocess.run(["git", "reset", "--hard"], cwd=self.directory_path(), check=True)
                subprocess.run(["git", "clean", "-fdx"], cwd=self.directory_path(), check=True)
                self.update_status(wait=wait)
                self.update_logs(wait=wait)
            except Exception as e:
                print(f"DISCARD EXCEPTION: {e}")
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        if wait:
            thread.join()

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

