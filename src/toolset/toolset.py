from __future__ import annotations
import os, uuid, shutil, tempfile, threading, stat, time, subprocess
from gi.repository import Gtk, GLib, Adw
from typing import final, ClassVar
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from collections import namedtuple
from abc import ABC, abstractmethod
from urllib.parse import ParseResult
from .root_function import root_function
from .runtime_env import RuntimeEnv
from .toolset_env_builder import ToolsetEnvBuilder
from .architecture import Architecture
from .event_bus import EventBus

@final
class Toolset:
    """Class containing details of the Toolset instances."""
    """Only metadata, no functionalities."""
    """Functionalities are handled by ToolsetContainer."""
    def __init__(self, env: ToolsetEnv, uuid: UUID, **kwargs):
        self.uuid = uuid
        self.env = env
        match env:
            case ToolsetEnv.SYSTEM:
                pass
            case ToolsetEnv.EXTERNAL:
                self.squashfs_file = kwargs.get("squashfs_file")
                if not isinstance(self.squashfs_file, str):
                    raise ValueError("EXTERNAL requires a 'squashfs_file' keyword argument (str)")
            case _:
                raise ValueError(f"Unknown env: {env}")
        self.access_lock = threading.Lock()
        self.spawned = False # Spawned means that directories in /tmp are prepared to be used with bwrap.
        self.in_use = False # Is any bwrap instance currently running on this toolset.
        # Current spawn settings:
        self.store_changes: bool = False
        self.bind_options: List[str] | None = None # Binding options prepared in current spawn for bwrap command.
        self.additional_bindings: List[BindMount] | None = None
        self.hot_fixes: List[HotFix] | None = None
        self.work_dir: str | None = None

    @classmethod
    def init_from(cls, data: dict) -> Toolset:
        try:
            uuid_value = uuid.UUID(data["uuid"])
            env = ToolsetEnv[data["env"]]
        except KeyError:
            raise ValueError(f"Failed to parse {data}")
        kwargs = {}
        match env:
            case ToolsetEnv.SYSTEM:
                pass
            case ToolsetEnv.EXTERNAL:
                squashfs_file = data.get("squashfs_file")
                if not isinstance(squashfs_file, str):
                    raise ValueError("Missing or invalid 'squashfs_file' for EXTERNAL environment")
                kwargs["squashfs_file"] = squashfs_file
        return cls(env, uuid_value, **kwargs)

    def serialize(self) -> dict:
        data = {
            "uuid": str(self.uuid),
            "env": self.env.name
        }
        if self.env == ToolsetEnv.EXTERNAL:
            data["squashfs_file"] = self.squashfs_file
        return data

    @staticmethod
    def create_system() -> Toolset:
        """Create a Toolset with the SYSTEM environment."""
        return Toolset(ToolsetEnv.SYSTEM, uuid.uuid4())

    @staticmethod
    def create_external(squashfs_file: str) -> Toolset:
        """Create a Toolset with the EXTERNAL environment and a specified squashfs file."""
        return Toolset(ToolsetEnv.EXTERNAL, uuid.uuid4(), squashfs_file=squashfs_file)

    def is_allowed_in_current_host() -> bool:
        return self.env.is_running_in_gentoo_host()

    def toolset_root(self) -> str:
        match self.env:
            case ToolsetEnv.SYSTEM:
                return "/"
            case ToolsetEnv.EXTERNAL:
                return self.squashfs_file # TODO: For now squashfs_file and root dir are mixed concepts.
    # --------------------------------------------------------------------------
    # Spawning cycle:

    def spawn(self, store_changes: bool = False, hot_fixes: List[HotFix] | None = None, additional_bindings: List[BindMount] | None = None):
        """Prepare /tmp folders for bwrap calls."""
        with self.access_lock:
            if self.spawned:
                raise RuntimeError(f"Toolset {self} already spawned.")

            # Prepare /tmp directories and bind_options
            runtime_env = RuntimeEnv.current()

            resolved_toolset_root = str(Path(self.toolset_root()).resolve())
            if resolved_toolset_root == "/" and store_changes:
                raise RuntimeError("Cannot use store_changes with host toolset")

            _system_bindings = [ # System.
                BindMount(mount_path="/usr",   toolset_path="/usr",   store_changes=store_changes),
                BindMount(mount_path="/bin",   toolset_path="/bin",   store_changes=store_changes),
                BindMount(mount_path="/sbin",  toolset_path="/sbin",  store_changes=store_changes),
                BindMount(mount_path="/lib",   toolset_path="/lib",   store_changes=store_changes),
                BindMount(mount_path="/lib32", toolset_path="/lib32", store_changes=store_changes),
                BindMount(mount_path="/lib64", toolset_path="/lib64", store_changes=store_changes),
            ]
            _devices_bindings = [ # Devices.
                BindMount(mount_path="/dev/kvm", host_path="/dev/kvm", store_changes=True) # Store changes is added only to use --dev-bind flag
            ]
            _config_bindings = [ # Config.
                BindMount(mount_path="/etc", toolset_path="/etc", store_changes=store_changes),
                BindMount(mount_path="/etc/resolv.conf", host_path="/etc/resolv.conf"), # Take resolv.conf directly from main system
            ]
            _working_bindings = [ # Working.
                BindMount(mount_path="/var", toolset_path="/var", store_changes=store_changes),
                BindMount(mount_path="/tmp"), # Create empty tmp when running env
            ]
            # All bindings.
            bindings = ( _system_bindings + _config_bindings + _devices_bindings + _working_bindings + (additional_bindings or []) )

            # Map bindings using toolset_path to host_path.
            for bind in bindings:
                if bind.host_path and bind.toolset_path:
                    raise ValueError(f"BindMount for mount_path '{bind.mount_path}' has both host_path and toolset_path set. Only one is allowed.")
                if bind.toolset_path:
                    bind.host_path = os.path.join(resolved_toolset_root, bind.toolset_path.lstrip("/"))

            work_dir = Path(tempfile.mkdtemp(prefix="gentoo_toolset_spawn_"))
            bind_options = []
            try:
                OverlayPaths = namedtuple("OverlayPaths", ["upper", "work"])

                # Prepare work dirs:
                fake_root = os.path.join(work_dir, "fake_root")
                overlay_root = os.path.join(work_dir, "overlay")
                hotfixes_workdir = os.path.join(work_dir, "hotfixes") # Stores patched files if needed
                os.makedirs(fake_root, exist_ok=False)
                os.makedirs(overlay_root, exist_ok=False)
                for field in OverlayPaths._fields: # Creates upper and work subdirectories.
                    os.makedirs(os.path.join(overlay_root, field), exist_ok=False)
                os.makedirs(hotfixes_workdir, exist_ok=False)

                # Collect required hotfix patched files and add to bindings:
                hotfix_patches = [fix.get_patch_spec for fix in (hot_fixes or [])]
                for patch in hotfix_patches:
                    patched_file_path = apply_patch_and_store_for_isolated_system(runtime_env, resolved_toolset_root, hotfixes_workdir, patch)
                    if patched_file_path is not None:
                        # Convert patch file to BindMount structure
                        patched_file_binding = BindMount(mount_path=patch.source_path, host_path=patched_file_path, resolve_host_path=False)
                        bindings.append(patched_file_binding)

                # Name overlay entries using indexes to avoid overlaps.
                mapping_index=0

                # Creates entry in overlay that maps other directory.
                def create_overlay_map(mount_path: str) -> OverlayPaths:
                    nonlocal mapping_index
                    # Create directories for all fields in OverlayPaths with given mount_path (upper, work [lower is considered mapped directory])
                    map_name=mount_path.replace("/", "_")
                    values = {
                        field: f"{overlay_root}/{field}/{mapping_index}{map_name}".replace("//", "/")
                        for field in OverlayPaths._fields
                    }
                    for path in values.values():
                        os.makedirs(path, exist_ok=False)
                    mapping_index+=1
                    return OverlayPaths(**values)
                # Creates entry in overlay for temp dir, without mapping other directory.
                def create_overlay_temp(mount_path: str) -> str:
                    nonlocal mapping_index
                    map_name=mount_path.replace("/", "_")
                    overlay_mount_path=f"{overlay_root}/upper/{mapping_index}{map_name}".replace("//", "/")
                    os.makedirs(overlay_mount_path, exist_ok=False)
                    mapping_index+=1
                    return overlay_mount_path

                # Bind files and directories specified in bindings inside fake_root:
                for binding in bindings:
                    resolved_host_path = ( # Used to check if exists through current runtime env (works with flatpak env)
                        None if binding.host_path is None else
                        runtime_env.resolve_path_for_host_access(binding.host_path) if binding.resolve_host_path else binding.host_path
                    )
                    # Handle not existing host paths.
                    if binding.host_path is not None and not os.path.exists(resolved_host_path):
                        # Create in host is store_changes is set.
                        if binding.store_changes:
                            print(f"Path {resolved_host_path} not found. Creating directory in host.")
                            os.makedirs(resolved_host_path)
                        # Skip not existing bindings with host_path set:
                        else:
                            print(f"Path {resolved_host_path} not found. Skipping binding.")
                            continue # or raise an error if that's preferred

                    # Empty writable dirs:
                    if binding.host_path is None:
                        tmp_path = create_overlay_temp(binding.mount_path)
                        bind_options.extend(["--bind", tmp_path, binding.mount_path])
                        continue
                    # Symlinks (keep as symlinks in isolated env):
                    if resolved_host_path is not None and os.path.islink(resolved_host_path):
                        target = os.readlink(resolved_host_path)
                        fake_symlink_path = os.path.join(fake_root, binding.mount_path.lstrip("/"))
                        os.makedirs(os.path.dirname(fake_symlink_path), exist_ok=True)
                        os.symlink(target, fake_symlink_path)
                        continue
                    # Char devices:
                    if stat.S_ISCHR(os.stat(resolved_host_path).st_mode):
                        flag = "--dev-bind" if binding.store_changes else "--ro-bind"
                        bind_options.extend([flag, binding.host_path, binding.mount_path])
                        continue
                    # Standard files:
                    if stat.S_ISREG(os.stat(resolved_host_path).st_mode):
                        flag = "--bind" if binding.store_changes else "--ro-bind"
                        bind_options.extend([flag, binding.host_path, binding.mount_path])
                        continue
                    # Directories:
                    if stat.S_ISDIR(os.stat(resolved_host_path).st_mode):
                        if binding.store_changes:
                            bind_options.extend(["--bind", binding.host_path, binding.mount_path])
                        else:
                            overlay = create_overlay_map(binding.mount_path)
                            bind_options.extend([
                                "--overlay-src", binding.host_path,
                                "--overlay", overlay.upper, overlay.work, binding.mount_path
                            ])
                        continue
            except Exception as e:
                shutil.rmtree(work_dir, ignore_errors=True)
                raise e

            self.work_dir = work_dir
            self.hot_fixes = hot_fixes
            self.additional_bindings = additional_bindings
            self.store_changes = store_changes
            self.bind_options = bind_options
            self.spawned = True

    def unspawn(self):
        """Clear /tmp folders."""
        with self.access_lock:
            if not self.spawned:
                raise RuntimeError(f"Toolset {self} is not spawned.")
            if self.in_use:
                raise RuntimeError(f"Toolset {self} is currently in use.")
            # Remove /tmp folders. TODO: After root_calls this dirs contains root owned files and directories, making it impossible to remove as user.
            if self.work_dir:
                shutil.rmtree(self.work_dir, ignore_errors=True)
            # Reset spawned settings:
            self.work_dir = None
            self.hot_fixes = None
            self.additional_bindings = None
            self.store_changes = False
            self.bind_options = None
            self.spawned = False

    # --------------------------------------------------------------------------
    # Calling commands:

    def run_command(self, command: str, handler: callable | None = None, completion_handler: callable | None = None) -> ServerCall:
        # TODO: Add required parameters checks, like store_changes matches spawned env, required bindings are set correctly etc.
        with self.access_lock:
            if not self.spawned:
                raise RuntimeError(f"Toolset {self} is not spawned.")
            if self.in_use:
                raise RuntimeError(f"Toolset {self} is currently in use.")
            self.in_use = True

            def on_complete(completion_handler: callable | None, result: ServerResponse):
                with self.access_lock:
                    self.in_use = False
                if completion_handler:
                    completion_handler(result)
            try:
                fake_root = os.path.join(self.work_dir, "fake_root")
                return _start_toolset_command._async_raw(
                    handler=handler,
                    # Wraps completion block to set in_use flag additionally after it's done
                    completion_handler=lambda x: on_complete(completion_handler, x),
                    work_dir=str(self.work_dir),
                    fake_root=fake_root,
                    bind_options=self.bind_options,
                    command_to_run=command
                )
            except Exception as e:
                print(f"Failed to execute command: {e}")
                self.in_use = False
                raise e

@dataclass
class BindMount:
    mount_path: str                 # Mount location inside the isolated environment.
    host_path: str | None = None    # None if mount point is an empty dir from overlay.
    toolset_path: str | None = None # Host path relative to toolset root.
    store_changes: bool = False     # True if changes should be stored outside isolated env.
    resolve_host_path: bool = True  # Whether to resolve path through runtime_env.

@root_function
def _start_toolset_command(work_dir: str, fake_root: str, bind_options: List[str], command_to_run: str):
    import subprocess
    #subprocess.run(["chown", "-R", "root:root", work_dir], check=True) # This could change the ownership of work_dir for root, but probably is not needed.
    cmd_bwrap = (
        "bwrap "
        "--die-with-parent "
        "--unshare-uts --unshare-ipc --unshare-pid --unshare-cgroup "
        "--hostname catalyst-lab "
        "--bind " + fake_root + " / "
        "--dev /dev "
        "--proc /proc "
        "--setenv HOME / "
    )
    arguments_string = " ".join(bind_options) + " /bin/sh -c '" + command_to_run + "'"
    exec_call = cmd_bwrap + arguments_string
    try:
        result = subprocess.run(exec_call, shell=True).returncode
        if result != 0:
            raise RuntimeError(f"Toolset call returned exit code: {result}")
    except Exception as e:
        # Note: We don't handle exceptions here, because if the root function throws,
        # the exception will be just returned as a result of this call, which is what we want.
        raise e

@final
class ToolsetEnv(Enum):
    SYSTEM   = auto() # Using tools from system, either through HOST or FLATPAK RuntimeEnv.
    EXTERNAL = auto() # Using tools from given .squashfs installation.

    def is_allowed_in_current_host(self) -> bool:
        """Can selected ToolsetEnv be used in current host. SYSTEM only allowed in gentoo, EXTERNAL allowed anywhere."""
        match self:
            case ToolsetEnv.SYSTEM:
                return RuntimeEnv.is_running_in_gentoo_host()
            case ToolsetEnv.EXTERNAL:
                return True

# ------------------------------------------------------------------------------
# Toolset installation.
# ------------------------------------------------------------------------------
@final
class ToolsetInstallationEvent(Enum):
    """Events produced by installation."""
    STATE_CHANGED = auto()

class ToolsetInstallation:
    """Handles the full toolset installation lifecycle."""

    # Remembers installations started in current app cycle. Never removed, even after finishing.
    started_installations: list[ToolsetInstallation] = []

    def __init__(self, stage_url: ParseResult, allow_binpkgs: bool, selected_apps: list[ToolsetApplication]):
        self.stage_url = stage_url
        self.allow_binpkgs = allow_binpkgs
        self.selected_apps = selected_apps
        self.steps: list[ToolsetInstallationStep] = []
        self.event_bus: EventBus[ToolsetInstallationEvent] = EventBus[ToolsetInstallationEvent]()
        self.status = ToolsetInstallationStage.INSTALL
        self._setup_steps()

    def _setup_steps(self):
        self.steps.append(ToolsetInstallationStepDownload(url=self.stage_url, installer=self))
        self.steps.append(ToolsetInstallationStepExtract(installer=self))
        self.steps.append(ToolsetInstallationStepSpawn(installer=self))
        self.steps.append(ToolsetInstallationStepUpdatePortage(installer=self))
        for app in self.selected_apps:
            self.steps.append(ToolsetInstallationStepInstallApp(app=app, installer=self))
        self.steps.append(ToolsetInstallationStepVerify(installer=self))
        self.steps.append(ToolsetInstallationStepCleanup(installer=self))
        self.steps.append(ToolsetInstallationStepCompress(installer=self))

    def start(self):
        ToolsetInstallation.started_installations.append(self)
        self._continue_installation()

    def _continue_installation(self):
        next_step = next((step for step in self.steps if step.state == ToolsetInstallationStepState.SCHEDULED), None)
        failed_step = next((step for step in self.steps if step.state == ToolsetInstallationStepState.FAILED), None)
        if failed_step:
            self.status = ToolsetInstallationStage.FAILED
            self.event_bus.emit(ToolsetInstallationEvent.STATE_CHANGED, self.status)
        elif next_step:
            next_step_thread = threading.Thread(target=next_step.start)
            next_step_thread.start()
        else:
            self.status = ToolsetInstallationStage.COMPLETED
            self.event_bus.emit(ToolsetInstallationEvent.STATE_CHANGED, self.status)

class ToolsetInstallationStage(Enum):
    """Current state of installation."""
    SETUP = auto()     # Installation not started yet, still collecting details.
    INSTALL = auto()   # Installing toolset (whole process).
    COMPLETED = auto() # Toolset created sucessfully.
    FAILED = auto()    # Toolset failed at any step.

# ------------------------------------------------------------------------------
# Toolset applications.

@dataclass(frozen=True)
class ToolsetApplication:
    """Additional tools installed in toolsets, like Catalyst, Qemu."""
    ALL: ClassVar[list[ToolsetApplication]] = []
    name: str
    description: str
    is_recommended: bool
    is_highly_recommended: bool
    def __post_init__(self):
        # Automatically add new instances to ToolsetApplication.ALL
        ToolsetApplication.ALL.append(self)

ToolsetApplication.CATALYST = ToolsetApplication(name="Catalyst", description="Required to build Gentoo stages", is_recommended=True, is_highly_recommended=True)
ToolsetApplication.QEMU = ToolsetApplication(name="Qemu", description="Allows building stages for different architectures", is_recommended=True, is_highly_recommended=False)

# ------------------------------------------------------------------------------
# Installation process steps.

class ToolsetInstallationStepState(Enum):
    """Stage of single installation step."""
    SCHEDULED = auto()   # Step scheduled for execution.
    IN_PROGRESS = auto() # Step started and is in progress.
    COMPLETED = auto()   # Step completed successfully.
    FAILED = auto()      # Step failed.

@final
class ToolsetInstallationStepEvent(Enum):
    """Events produced by installation steps."""
    STATE_CHANGED = auto()

class ToolsetInstallationStep(ABC):
    """Base class for toolset installation steps."""
    def __init__(self, name: str, description: str, installer: ToolsetCreateView):
        self.state = ToolsetInstallationStepState.SCHEDULED
        self.name = name
        self.description = description
        self.installer = installer
        self.event_bus: EventBus[ToolsetInstallationStepEvent] = EventBus[ToolsetInstallationStepEvent]()
    @abstractmethod
    def start(self):
        self._update_state(ToolsetInstallationStepState.IN_PROGRESS)
    def complete(self, state: ToolsetInstallationStepState):
        """Call this when step finishes."""
        self._update_state(state=state)
        # If state was success continue installation
        GLib.idle_add(self.installer._continue_installation)
    def _update_state(self, state: ToolsetInstallationStepState):
        self.state = state
        self.event_bus.emit(ToolsetInstallationStepEvent.STATE_CHANGED, state)

# Steps implementations:

class ToolsetInstallationStepDownload(ToolsetInstallationStep):
    def __init__(self, url: ParseResult, installer: ToolsetCreateView):
        super().__init__(name="Download stage tarball", description="Downloading Gentoo stage tarball", installer=installer)
        self.url = url
    def start(self):
        super().start()
        print(f"Downloading {self.url} ...")
        time.sleep(1)
        self.complete(ToolsetInstallationStepState.COMPLETED)

class ToolsetInstallationStepExtract(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Extract stage tarball", description="Extracts Gentoo stage tarball to work directory", installer=installer)
    def start(self):
        super().start()
        print(f"Extracting ...")
        time.sleep(1)
        self.complete(ToolsetInstallationStepState.COMPLETED)

class ToolsetInstallationStepSpawn(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Spawn environment", description="Prepares Gentoo environment for work", installer=installer)
    def start(self):
        super().start()
        print(f"Spawning ...")
        time.sleep(1)
        self.complete(ToolsetInstallationStepState.COMPLETED)

class ToolsetInstallationStepUpdatePortage(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Update portage", description="Synchronizes portage tree", installer=installer)
    def start(self):
        super().start()
        print(f"Syncing ...")
        time.sleep(1)
        self.complete(ToolsetInstallationStepState.COMPLETED)

class ToolsetInstallationStepInstallApp(ToolsetInstallationStep):
    def __init__(self, app: ToolsetApplication, installer: ToolsetCreateView):
        super().__init__(name=f"Install {app.name}", description=f"Configures and emerges {app.name}", installer=installer)
        self.app = app
    def start(self):
        super().start()
        print(f"Installing {self.app.name} ...")
        time.sleep(1)
        self.complete(ToolsetInstallationStepState.COMPLETED)

class ToolsetInstallationStepVerify(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Verify stage", description="Checks if toolset works correctly", installer=installer)
    def start(self):
        super().start()
        print(f"Verifying ...")
        time.sleep(1)
        self.complete(ToolsetInstallationStepState.COMPLETED)

class ToolsetInstallationStepCompress(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Compress", description="Compresses toolset into .squashfs file", installer=installer)
    def start(self):
        super().start()
        print(f"Compressing ...")
        time.sleep(1)
        self.complete(ToolsetInstallationStepState.COMPLETED)

class ToolsetInstallationStepCleanup(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Cleanup", description="Unspawns toolset and cleans up", installer=installer)
    def start(self):
        super().start()
        print(f"Cleaning ...")
        time.sleep(1)
        self.complete(ToolsetInstallationStepState.COMPLETED)

