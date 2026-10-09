from __future__ import annotations
import os, threading, uuid
from typing import Self
from .event_bus import EventBus, SharedEvent
from .repository import Repository
from .status_indicator import StatusIndicatorState, StatusIndicatorValues
from .lima import list_instances, run_limactl, MACHINE_DATA_DIRECTORY

class BuildMachine:
    """Virtual machine (Lima instance) where toolsets run, on systems that can't build stages themselves (macOS).
    Toolsets linked to machine are spawned inside it, so their builds, updates and snapshots run there too."""

    STATUS_RUNNING = "Running"
    STATUS_STOPPED = "Stopped"
    STATUS_MISSING = "Missing"

    def __init__(self, id: uuid.UUID | None = None, name: str = "", cpus: int = 4, memory_gib: int = 4, workspace_gib: int = 100):
        self.id = id or uuid.uuid4()
        self.name = name
        self.cpus = cpus
        self.memory_gib = memory_gib
        self.workspace_gib = workspace_gib # Maximum size of working space image (it grows only as it's used).
        self.event_bus = EventBus()
        self._status: str | None = None
        self._lock = threading.Lock()
        self._workspace_lock = threading.Lock()
        self._workspace_users = 0

    @property
    def instance_name(self) -> str:
        """Name of Lima instance."""
        return f"catalystlab-{self.id.hex[:8]}" # Short, Lima socket paths are limited to 104 characters.

    def serialize(self) -> dict:
        return {"id": str(self.id), "name": self.name, "cpus": self.cpus, "memory_gib": self.memory_gib, "workspace_gib": self.workspace_gib}

    @classmethod
    def init_from(cls, data: dict) -> Self:
        return cls(id=uuid.UUID(data["id"]), name=data["name"], cpus=data.get("cpus", 4),
                   memory_gib=data.get("memory_gib", 4), workspace_gib=data.get("workspace_gib", 100))

    # Status:

    @property
    def status(self) -> str:
        if self._status is None:
            self.refresh_status()
        return self._status

    def refresh_status(self):
        instance = list_instances().get(self.instance_name)
        status = instance.get("status", self.STATUS_STOPPED) if instance else self.STATUS_MISSING
        if status != self._status:
            self._status = status
            self.event_bus.emit(SharedEvent.STATE_UPDATED, self)

    @property
    def is_running(self) -> bool:
        return self.status == self.STATUS_RUNNING

    @property
    def short_details(self) -> str:
        return f"{self.status} · {self.cpus} CPUs, {self.memory_gib} GiB memory, up to {self.workspace_gib} GiB working space"

    @property
    def status_indicator_values(self) -> StatusIndicatorValues:
        return StatusIndicatorValues(
            state=StatusIndicatorState.ENABLED if self.status == self.STATUS_RUNNING else StatusIndicatorState.DISABLED,
            blinking=False
        )

    # Lifecycle:

    def start(self, output_handler=print, process_holder: list | None = None) -> bool:
        with self._lock:
            self.refresh_status()
            if self.is_running:
                return True
            if self.status == self.STATUS_MISSING:
                raise RuntimeError(f"Virtual machine {self.name} doesn't exist anymore")
            output_handler(f"Starting virtual machine {self.name}...")
            success = run_limactl(["start", self.instance_name], output_handler, process_holder)
            self.refresh_status()
            return success and self.is_running

    def stop(self, output_handler=print) -> bool:
        with self._lock:
            success = run_limactl(["stop", self.instance_name], output_handler)
            self.refresh_status()
            return success

    def ensure_running(self, output_handler=print, process_holder: list | None = None):
        """Starts machine if needed, raises when it can't be started."""
        if not self.start(output_handler, process_holder):
            raise RuntimeError(f"Failed to start virtual machine {self.name}")

    # Working space:
    # Ext4 image in shared folder, mounted in machine at MACHINE_DATA_DIRECTORY while operations use it (environments,
    # builds, installations). It's created for first operation and deleted when last one finishes, so data doesn't
    # stay in machine nor take space on this computer.

    @property
    def workspace_image_path(self) -> str:
        toolsets_location = os.path.realpath(os.path.expanduser(Repository.Settings.value.toolsets_location))
        return os.path.join(os.path.dirname(toolsets_location), "Machines", self.instance_name, "workspace.img")

    def acquire_workspace(self, output_handler=print, process_holder: list | None = None):
        """Prepares working space for operation, call release_workspace when it finishes."""
        with self._workspace_lock:
            if self._workspace_users == 0:
                self._create_workspace(output_handler, process_holder)
            self._workspace_users += 1

    def release_workspace(self, output_handler=print):
        with self._workspace_lock:
            if self._workspace_users == 0:
                return
            self._workspace_users -= 1
            if self._workspace_users == 0:
                self._delete_workspace(output_handler)

    def _create_workspace(self, output_handler, process_holder):
        from .rootless import MachineExecutor
        image = self.workspace_image_path
        os.makedirs(os.path.dirname(image), exist_ok=True)
        output_handler(f"Preparing working space of {self.name}")
        # Left by previous session of app (eg. after crash) is replaced.
        script = f"""
set -e
sudo umount -l {MACHINE_DATA_DIRECTORY} 2>/dev/null || true
rm -f "{image}"
truncate -s {self.workspace_gib}G "{image}"
mkfs.ext4 -q -F -m 0 "{image}"
sudo mount -o loop "{image}" {MACHINE_DATA_DIRECTORY}
sudo chown "$(id -u):$(id -g)" {MACHINE_DATA_DIRECTORY}
"""
        if not MachineExecutor(self).run(script, output_handler, process_holder):
            raise RuntimeError(f"Failed to prepare working space of {self.name}")

    def _delete_workspace(self, output_handler):
        from .rootless import MachineExecutor
        image = self.workspace_image_path
        output_handler(f"Removing working space of {self.name}")
        try:
            if self.is_running:
                MachineExecutor(self).run(f'sudo umount -l {MACHINE_DATA_DIRECTORY}; rm -f "{image}"', output_handler)
        except Exception as e:
            output_handler(f"Failed to unmount working space: {e}")
        if os.path.exists(image):
            os.remove(image)

def build_machine_for_id(machine_id: uuid.UUID | None) -> BuildMachine | None:
    if machine_id is None:
        return None
    return next((machine for machine in Repository.BuildMachine.value if machine.id == machine_id), None)
