# Managing GIT based directory (local or remote). Base for RelengDirectory and
# OverlayDirectory.
from __future__ import annotations
import os, threading, subprocess, uuid
from enum import Enum, auto
from datetime import datetime
from .repository import Serializable
from .event_bus import EventBus, SharedEvent
from .status_indicator import StatusIndicatorState, StatusIndicatorValues
from abc import ABC, abstractmethod

class GitDirectoryEvent(Enum):
    LOGS_CHANGED = auto()
    NAME_CHANGED = auto

class GitDirectoryStatus(Enum):
    # Git status
    UNKNOWN = auto()
    UNCHANGED = auto()
    CHANGED = auto()

class GitDirectory(Serializable, ABC):

    # Overwrite in subclassed
    @classmethod
    @abstractmethod
    def base_location(cls) -> str:
        pass

    def __init__(
        self, name: str,
        id: uuid.UUID | None = None,
        branch_name: str | None = None,
        last_commit_date: datetime | None = None,
        remote_url: str | None = None,
        has_remote_changes: bool = False,
        metadata: Serializable | None = None
    ):
        self.name = name
        self.id = id or uuid.uuid4()
        self.status: GitDirectoryStatus = GitDirectoryStatus.UNKNOWN
        self.last_commit_date = last_commit_date
        self.branch_name = branch_name
        self.remote_url = remote_url
        self.has_remote_changes = has_remote_changes
        self.metadata = metadata
        self.logs: list[dict] = []
        self.event_bus = EventBus[GitDirectoryEvent]()

    @property
    def short_details(self) -> str:
        parts = []
        if self.branch_name:
            parts.append(self.branch_name)
        if self.last_commit_date:
            parts.append(self.last_commit_date.strftime('%Y-%m-%d %H:%M'))
        return ", ".join(parts)

    @property
    def status_indicator_values(self) -> StatusIndicatorValues:
        match self.status:
            # TODO: Add new color when updates are available, here and in other classes.
            case GitDirectoryStatus.UNKNOWN | GitDirectoryStatus.UNCHANGED:
                return StatusIndicatorValues(
                    state=StatusIndicatorState.DISABLED,
                    blinking=False
                )
            case GitDirectoryStatus.CHANGED:
                return StatusIndicatorValues(
                    state=StatusIndicatorState.ENABLED_UNSAFE,
                    blinking=False
                )
            case _:
                return StatusIndicatorValues(
                    state=StatusIndicatorState.DISABLED,
                    blinking=False
                )

    @classmethod
    def parse_metadata(cls, dict: dict) -> Serializable:
        """Overwrite in subclasses that use metadata"""
        return None

    def serialize(self) -> dict:
        return {
            "name": self.name,
            "id": str(self.id),
            "last_commit_date": self.last_commit_date.isoformat() if self.last_commit_date else None,
            "remote_url": self.remote_url,
            "branch_name": self.branch_name,
            "has_remote_changes": self.has_remote_changes,
            "metadata": self.metadata.serialize() if self.metadata else None
        }

    @classmethod
    def init_from(cls, data: dict) -> Self:
        try:
            name = data["name"]
            id_value = uuid.UUID(data["id"])
            last_commit_date = (
                datetime.fromisoformat(data["last_commit_date"])
                if data.get("last_commit_date") else None
            )
            remote_url = data.get("remote_url")
            branch_name = data.get("branch_name")
            has_remote_changes = data.get("has_remote_changes")
            metadata = (
                cls.parse_metadata(dict=data.get("metadata"))
                if data.get("metadata") else None
            )
        except KeyError:
            raise ValueError(f"Failed to parse {data}")
        return cls(
            name=name,
            id=id_value,
            last_commit_date=last_commit_date,
            remote_url=remote_url,
            branch_name=branch_name,
            has_remote_changes=has_remote_changes,
            metadata=metadata
        )

    def update_status(self, wait: bool = False):
        def worker():
            directory = self.directory_path()
            if not os.path.isdir(directory) or not os.path.isdir(
                os.path.join(directory, ".git")
            ):
                self.status = GitDirectoryStatus.UNKNOWN
                self.last_commit_date = None
                self.branch_name = None
                self.event_bus.emit(SharedEvent.STATE_UPDATED, self)
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
                    self.status = GitDirectoryStatus.UNKNOWN
                else:
                    self.status = GitDirectoryStatus.CHANGED if stdout.strip() else GitDirectoryStatus.UNCHANGED
                # Get last commit date (ISO 8601)
                process_date = subprocess.Popen(
                    ["git", "log", "-1", "--format=%cI"],
                    cwd=directory,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True
                )
                last_commit_date, _ = process_date.communicate()
                if last_commit_date:
                    self.last_commit_date = datetime.fromisoformat(
                        last_commit_date.strip()
                    )
                else:
                    self.last_commit_date = None
                # Get current branch name
                process_branch = subprocess.Popen(
                    ["git", "symbolic-ref", "--short", "HEAD"],
                    cwd=directory,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True
                )
                branch_name, _ = process_branch.communicate()
                self.branch_name = branch_name.strip() if process_branch.returncode == 0 else None
                # Get remote URL
                process_remote = subprocess.Popen(
                    ["git", "remote", "get-url", "origin"],
                    cwd=directory,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True
                )
                remote_url, _ = process_remote.communicate()
                self.remote_url = remote_url.strip() if process_remote.returncode == 0 else None
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
                        [
                            "git",
                            "rev-list",
                            "--count",
                            "--left-only",
                            "@{u}...HEAD"
                        ],
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
                        self.has_remote_changes = False
                except Exception as e:
                    print(f"Warning: Failed to check for updates: {e}")
                    self.has_remote_changes = False
            except Exception as e:
                print(f"STATUS EXCEPTION: {e}")
                self.status = GitDirectoryStatus.UNKNOWN
                self.last_commit_date = None
                self.branch_name = None
            self.event_bus.emit(SharedEvent.STATE_UPDATED, self)
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        if wait:
            thread.join()

    def update_logs(self, wait: bool = False):
        def worker():
            directory = self.directory_path()
            logs = []
            if not os.path.isdir(directory) or not os.path.isdir(
                os.path.join(directory, ".git")
            ):
                self.logs = []
                self.event_bus.emit(GitDirectoryEvent.LOGS_CHANGED, self.logs)
                return
            try:
                # Use a custom format to make parsing easier
                log_format = "%H%x1f%an%x1f%aI%x1f%s"
                process = subprocess.Popen(
                    [
                        "git",
                        "log",
                        f"--format={log_format}"
                    ],
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
                        logs.append({
                            "hash": commit_hash,
                            "author": author,
                            "date": date,
                            "message": message,
                        })
            except Exception as e:
                print(f"LOG EXCEPTION: {e}")
                self.logs = []
            self.logs = logs
            self.event_bus.emit(GitDirectoryEvent.LOGS_CHANGED, self.logs)
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        if wait:
            thread.join()

    def discard_changes(self, wait: bool = False):
        def worker():
            try:
                subprocess.run(
                    ["git", "reset", "--hard"],
                    cwd=self.directory_path(),
                    check=True
                )
                subprocess.run(
                    ["git", "clean", "-fdx"],
                    cwd=self.directory_path(),
                    check=True
                )
                self.update_status(wait=wait)
                self.update_logs(wait=wait)
            except Exception as e:
                print(f"DISCARD EXCEPTION: {e}")
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        if wait:
            thread.join()

    def commit_changes(self, wait: bool = False):
        def worker():
            try:
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=self.directory_path(),
                    check=True
                )
                subprocess.run(
                    ["git", "commit", "-m", "Save changes"],
                    cwd=self.directory_path(),
                    check=True
                )
                self.update_status(wait=wait)
                self.update_logs(wait=wait)
            except Exception as e:
                print(f"COMMIT EXCEPTION: {e}")
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        if wait:
            thread.join()

    @classmethod
    def directory_path_for_name(cls, name: str) -> str:
        return os.path.join(
            cls.base_location(),
            cls.sanitized_name_for_name(name)
        )

    def directory_path(self) -> str:
        return self.__class__.directory_path_for_name(self.name)

    @staticmethod
    def sanitized_name_for_name(name: str) -> str:
        def sanitize_filename_linux(name: str) -> str:
            return name.replace('/', '_').replace('\0', '_')
        return sanitize_filename_linux(name=name)

    def sanitized_name(self) -> str:
        return self.sanitized_name_for_name(self.name)

