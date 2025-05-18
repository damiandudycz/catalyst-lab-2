from __future__ import annotations
import os, uuid, shutil, tempfile, threading, stat, time, subprocess, requests, tarfile, re
from gi.repository import Gtk, GLib, Adw
from typing import final, ClassVar, Dict
from dataclasses import dataclass, field
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
from .root_helper_server import ServerResponse, ServerResponseStatusCode
from .hotfix_patching import HotFix, apply_patch_and_store_for_isolated_system

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
        self.bind_options: list[str] | None = None # Binding options prepared in current spawn for bwrap command.
        self.additional_bindings: list[BindMount] | None = None
        self.hot_fixes: list[HotFix] | None = None
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

    def spawn(self, store_changes: bool = False, hot_fixes: list[HotFix] | None = None, additional_bindings: list[BindMount] | None = None):
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
                BindMount(mount_path="/tmp", create_if_missing=True), # Create empty tmp when running env
                BindMount(mount_path="/var/db/repos", create_if_missing=True), # Keep portage tree in tmp directory
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
                        if binding.create_if_missing and binding.store_changes: # TODO: Think this logic through
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

    def analyze(self, handler: callable | None = None) -> bool:
        """Performs various sanity checks on toolset and stores gathered results."""
        """Returns true if all required checks succeeded."""
        return True
        # TODO ...

@dataclass
class BindMount:
    mount_path: str                 # Mount location inside the isolated environment.
    host_path: str | None = None    # None if mount point is an empty dir from overlay.
    toolset_path: str | None = None # Host path relative to toolset root.
    store_changes: bool = False     # True if changes should be stored outside isolated env.
    resolve_host_path: bool = True  # Whether to resolve path through runtime_env.
    create_if_missing: bool = False # Creates directory if not found on host.

@root_function
def _start_toolset_command(work_dir: str, fake_root: str, bind_options: list[str], command_to_run: str):
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
        "--setenv LANG C.UTF-8 "
        "--setenv LC_ALL C.UTF-8 "
    )
    arguments_string = " ".join(bind_options) + " bash -c '" + command_to_run + "'"
    exec_call = cmd_bwrap + arguments_string
    print(exec_call)
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

    def cancel(self):
        running_step = next((step for step in self.steps if step.state == ToolsetInstallationStepState.IN_PROGRESS), None)
        if running_step:
            running_step.cancel()

    def _cleanup(self):
        for step in reversed(self.steps): # Cleanup in reverse order
            step.cleanup()

    def _continue_installation(self):
        next_step = next((step for step in self.steps if step.state == ToolsetInstallationStepState.SCHEDULED), None)
        failed_step = next((step for step in self.steps if step.state == ToolsetInstallationStepState.FAILED), None)
        if failed_step:
            self.status = ToolsetInstallationStage.FAILED
            self.event_bus.emit(ToolsetInstallationEvent.STATE_CHANGED, self.status)
            self._cleanup()
        elif next_step:
            next_step_thread = threading.Thread(target=next_step.start)
            next_step_thread.start()
        else:
            self.status = ToolsetInstallationStage.COMPLETED
            self.event_bus.emit(ToolsetInstallationEvent.STATE_CHANGED, self.status)
            self._cleanup()

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
    package: str
    is_recommended: bool = False
    is_highly_recommended: bool = False
    portage_config: Tuple[ToolsetApplication.PortageConfig, ...] = field(default_factory=tuple)
    dependencies: Tuple[ToolsetApplication, ...] = field(default_factory=tuple)
    def __post_init__(self):
        # Automatically add new instances to ToolsetApplication.ALL
        ToolsetApplication.ALL.append(self)
    @dataclass(frozen=True)
    class PortageConfig:
         # eq: { "packages.use": ["Entry1", "Entry2"], "package.accept_keywords": ["Entry1", "Entry2"] }
        directory: str
        entries: Tuple[str, ...] = field(default_factory=tuple)

ToolsetApplication.CATALYST = ToolsetApplication(
    name="Catalyst", description="Required to build Gentoo stages",
    package="dev-util/catalyst",
    is_recommended=True, is_highly_recommended=True,
    portage_config=(
        ToolsetApplication.PortageConfig(directory="package.accept_keywords", entries=("dev-util/catalyst",)),
        ToolsetApplication.PortageConfig(
            directory="package.use",
            entries=(
                ">=sys-apps/util-linux-2.40.4 python",
                ">=sys-boot/grub-2.12-r6 grub_platforms_efi-64",
                ">=sys-boot/grub-2.12-r6 grub_platforms_efi-32",
            )
        ),
    )
)
ToolsetApplication.GENTOO_SOURCES = ToolsetApplication(
    name="Gentoo sources", description="Needed for qemu/cmake",
    package="sys-kernel/gentoo-sources",
    is_recommended=True
)
ToolsetApplication.QEMU = ToolsetApplication(
    name="Qemu", description="Allows building stages for different architectures",
    package="app-emulation/qemu",
    is_recommended=True,
    portage_config=(
        ToolsetApplication.PortageConfig(
            directory="package.use",
            entries=(
                "app-emulation/qemu static-user",
                "dev-libs/glib static-libs",
                "sys-libs/zlib static-libs",
                "sys-apps/attr static-libs",
                "dev-libs/libpcre2 static-libs",
                "sys-libs/libcap static-libs",
                "*/* QEMU_SOFTMMU_TARGETS: aarch64 aarch64_be alpha arm armeb hexagon hppa i386 loongarch64 m68k microblaze microblazeel mips mips64 mips64el mipsel mipsn32 mipsn32el or1k ppc ppc64 ppc64le riscv32 riscv64 s390x sh4 sh4eb sparc sparc32plus sparc64 x86_64 xtensa xtensaeb",
                "*/* QEMU_USER_TARGETS: aarch64 aarch64_be alpha arm armeb hexagon hppa i386 loongarch64 m68k microblaze microblazeel mips mips64 mips64el mipsel mipsn32 mipsn32el or1k ppc ppc64 ppc64le riscv32 riscv64 s390x sh4 sh4eb sparc sparc32plus sparc64 x86_64 xtensa xtensaeb",
            )
        ),
    ),
    dependencies=(ToolsetApplication.GENTOO_SOURCES,)
)

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
    PROGRESS_CHANGED = auto()

class ToolsetInstallationStep(ABC):
    """Base class for toolset installation steps."""
    def __init__(self, name: str, description: str, installer: ToolsetCreateView):
        self.state = ToolsetInstallationStepState.SCHEDULED
        self.name = name
        self.description = description
        self.installer = installer
        self.progress: float | None = None
        self.event_bus: EventBus[ToolsetInstallationStepEvent] = EventBus[ToolsetInstallationStepEvent]()
        self._cancel_event = threading.Event()
    @abstractmethod
    def start(self):
        self.server_call = None
        self._cancel_event.clear()
        self._update_state(ToolsetInstallationStepState.IN_PROGRESS)
    def cancel(self):
        if self.state == ToolsetInstallationStepState.IN_PROGRESS:
            self.complete(ToolsetInstallationStepState.FAILED)
            self._cancel_event.set()
    @abstractmethod
    def cleanup(self) -> bool:
        # TODO: Consider what to do if some cleaning fails, and next cleanings could mess up mounted files.
        # Probably it is fine tough, as all real mapping should be done by bwrap call and not on real filesystem.
        """Returns true if cleanup was needed and was started."""
        if self.state == ToolsetInstallationStepState.SCHEDULED:
            return False # No cleaning needed if job didn't start.
        self.cancel()
        return True
    def complete(self, state: ToolsetInstallationStepState):
        """Call this when step finishes."""
        if self._cancel_event.is_set():
            return
        self._update_state(state=state)
        if self.state == ToolsetInstallationStepState.COMPLETED:
           self._update_progress(1.0)
        # If state was success continue installation
        GLib.idle_add(self.installer._continue_installation)
    def _update_state(self, state: ToolsetInstallationStepState):
        self.state = state
        self.event_bus.emit(ToolsetInstallationStepEvent.STATE_CHANGED, state)
    def _update_progress(self, progress: float | None):
        self.progress = progress
        self.event_bus.emit(ToolsetInstallationStepEvent.PROGRESS_CHANGED, progress)
    def run_command_in_toolset(self, command: str, progress_handler: Callable[[str], float | None] | None = None) -> bool:
        try:
            self.server_call = None
            return_value = False
            done_event = threading.Event()
            def completion_handler(response: ServerResponse):
                nonlocal return_value
                return_value = response.code == ServerResponseStatusCode.OK
                done_event.set()
            def output_handler(output_line: str):
                print(output_line)
                progress = progress_handler(output_line)
                if progress is not None:
                    self._update_progress(progress)
            self.server_call = self.installer.tmp_toolset.run_command(
                command=command,
                handler=output_handler if progress_handler is not None else None,
                completion_handler=completion_handler
            )
            self.server_call.thread.join()
            done_event.wait()
            return return_value
        except Exception as e:
            print(f"Error synchronizing portage: {e}")
            self.complete(ToolsetInstallationStepState.FAILED)
            return False

# Steps implementations:

class ToolsetInstallationStepDownload(ToolsetInstallationStep):
    def __init__(self, url: ParseResult, installer: ToolsetCreateView):
        super().__init__(name="Download stage tarball", description="Downloading Gentoo stage tarball", installer=installer)
        self.url = url
    def start(self):
        super().start()
        try:
            response = requests.get(self.url.geturl(), stream=True, timeout=10)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            chunk_size = 1024 * 1024 # 1MB chunks.
            with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                self.installer.tmp_stage_file = tmp_file
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if self._cancel_event.is_set():
                        return
                    if chunk:
                        tmp_file.write(chunk)
                        downloaded += len(chunk)
                        if total_size:
                            progress = downloaded / total_size
                            self._update_progress(progress)
            self.complete(ToolsetInstallationStepState.COMPLETED)
        except Exception as e:
            print(f"Error during download: {e}")
            self.complete(ToolsetInstallationStepState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        if self.installer.tmp_stage_file:
            try:
                self.installer.tmp_stage_file.close()
                os.remove(self.installer.tmp_stage_file.name)
            except Exception as e:
                print(f"Failed to delete temp file: {e}")
        return True

class ToolsetInstallationStepExtract(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Extract stage tarball", description="Extracts Gentoo stage tarball to work directory", installer=installer)
    def start(self):
        super().start()
        try:
            self.installer.tmp_stage_extract_dir = tempfile.mkdtemp(prefix="gentoo_stage_extract_")
            return_value = False
            done_event = threading.Event()
            def completion_handler(response: ServerResponse):
                nonlocal return_value
                return_value = response.code == ServerResponseStatusCode.OK
                done_event.set()
            def output_handler(output_line: str):
                if output_line.startswith("PROGRESS: "):
                    try:
                        progress_str = output_line[len("PROGRESS: "):]
                        progress_value = float(progress_str)
                        self._update_progress(progress_value)
                    except ValueError:
                        pass
            self.server_call = extract._async_raw(
                handler=output_handler,
                completion_handler=completion_handler,
                tarball=self.installer.tmp_stage_file.name,
                directory=self.installer.tmp_stage_extract_dir
            )
            self.server_call.thread.join()
            done_event.wait()
            self.complete(ToolsetInstallationStepState.COMPLETED if return_value else ToolsetInstallationStepState.FAILED)
        except Exception as e:
            print(f"Error extracting stage tarball: {e}")
            self.complete(ToolsetInstallationStepState.FAILED)
    def cancel(self):
        super().cancel()
        if self.server_call:
            self.server_call.cancel()
            if self.server_call.thread:
                self.server_call.thread.join()
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        if self.installer.tmp_stage_extract_dir:
            try: # TODO: Needs to clean as root
                if os.path.exists(self.installer.tmp_stage_extract_dir):
                    shutil.rmtree(self.installer.tmp_stage_extract_dir)
            except Exception as e:
                print(f"Failed to delete temp folder: {e}")
        return True

class ToolsetInstallationStepSpawn(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Create environment", description="Prepares Gentoo environment for work", installer=installer)
    def start(self):
        super().start()
        try:
            self.installer.tmp_toolset = Toolset(ToolsetEnv.EXTERNAL, uuid.uuid4(), squashfs_file=self.installer.tmp_stage_extract_dir)
            self.installer.tmp_toolset.spawn(store_changes=True) # TODO: Add tmp dirs for portage etc., mount with store_changes
            commands = [
                "env-update && source /etc/profile",
                "getuto"
            ]
            for i, command in enumerate(commands):
                if self._cancel_event.is_set():
                    return
                print(f"# {command}")
                result = self.run_command_in_toolset(command=command)
                self._update_progress((i + 1) / len(commands))
                if not result:
                    self.complete(ToolsetInstallationStepState.FAILED)
                    return
            self.complete(ToolsetInstallationStepState.COMPLETED)
        except Exception as e:
            print(f"Error spawning temporary toolset: {e}")
            self.complete(ToolsetInstallationStepState.FAILED)
    def cancel(self):
        super().cancel()
        if self.server_call:
            self.server_call.cancel()
            if self.server_call.thread:
                self.server_call.thread.join()
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False

class ToolsetInstallationStepUpdatePortage(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Synchronize portage", description="Synchronizes portage tree", installer=installer)
    def start(self):
        super().start()
        try:
            def progress_handler(output_line: str) -> float or None:
                pattern = (
                    r"\s*"                        # optional leading spaces
                    r"\d+[KMGTP]?"                # downloaded size (e.g., 45500K, 4.47T)
                    r"\s+(?:\.{1,10}\s*)+"        # progress dots (at least one group)
                    r"(\d{1,3})%"                 # percentage (captured)
                    r"\s+\d+(\.\d+)?[KMGTP]?"     # speed (like 4.47T, 14.5M)
                    r"(?:[= ]\d+(\.\d+)?s?)?"     # optional time (e.g., =2.2s, 0s)"
                )
                match = re.match(pattern, output_line)
                if match:
                    return int(match.group(1)) / 100.0
            result = self.run_command_in_toolset(command="emerge-webrsync", progress_handler=progress_handler)
            self.complete(ToolsetInstallationStepState.COMPLETED if result else ToolsetInstallationStepState.FAILED)
        except Exception as e:
            print(f"Error synchronizing Portage: {e}")
            self.complete(ToolsetInstallationStepState.FAILED)
    def cancel(self):
        super().cancel()
        if self.server_call:
            self.server_call.cancel()
            if self.server_call.thread:
                self.server_call.thread.join()
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False

class ToolsetInstallationStepInstallApp(ToolsetInstallationStep):
    def __init__(self, app: ToolsetApplication, installer: ToolsetCreateView):
        super().__init__(name=f"Install {app.name}", description=f"Configures and emerges {app.name}", installer=installer)
        self.app = app
    def start(self):
        super().start()
        try:
            def progress_handler(output_line: str) -> float or None:
                pattern = r"^>>> Completed \((\d+) of (\d+)\)"
                match = re.match(pattern, output_line)
                if match:
                    n, m = map(int, match.groups())
                    return n / m
            for config in self.app.portage_config:
                if self._cancel_event.is_set():
                    return
                insert_portage_config(config_dir=config.directory, config_entries=config.entries, app_name=self.app.name, toolset_root=self.installer.tmp_toolset.toolset_root())
            flags = "--getbinpkg" if self.installer.allow_binpkgs else ""
            result = self.run_command_in_toolset(command=f"emerge {flags} {self.app.package}", progress_handler=progress_handler)
            self.complete(ToolsetInstallationStepState.COMPLETED if result else ToolsetInstallationStepState.FAILED)
        except Exception as e:
            print(f"Error during app installation: {e}")
            self.complete(ToolsetInstallationStepState.FAILED)
    def cancel(self):
        super().cancel()
        if self.server_call:
            self.server_call.cancel()
            if self.server_call.thread:
                self.server_call.thread.join()
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False

class ToolsetInstallationStepVerify(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Verify stage", description="Checks if toolset works correctly", installer=installer)
    def start(self):
        super().start()
        try:
            analysis_result = self.installer.tmp_toolset.analyze()
            self.complete(ToolsetInstallationStepState.COMPLETED if result else ToolsetInstallationStepState.FAILED)
        except Exception as e:
            print(f"Error during toolset verification: {e}")
            self.complete(ToolsetInstallationStepState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False

class ToolsetInstallationStepCompress(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Compress", description="Compresses toolset into .squashfs file", installer=installer)
    def start(self):
        super().start()
        try:
            time.sleep(1)
            self.complete(ToolsetInstallationStepState.COMPLETED)
        except Exception as e:
            print(f"Error during toolset compression: {e}")
            self.complete(ToolsetInstallationStepState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False

class ToolsetInstallationStepCleanup(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Cleanup", description="Unspawns toolset and cleans up", installer=installer)
    def start(self):
        super().start()
        try:
            time.sleep(1)
            self.complete(ToolsetInstallationStepState.COMPLETED)
        except Exception as e:
            print(f"Error during cleanup: {e}")
            self.complete(ToolsetInstallationStepState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
# ------------------------------------------------------------------------------
# Helper functions:

@root_function
def insert_portage_config(config_dir: str, config_entries: list[str], app_name: str, toolset_root: str):
    portage_dir = os.path.join(toolset_root, "etc", "portage", config_dir)
    os.makedirs(portage_dir, exist_ok=True)
    filename = app_name.replace("/", "_")
    config_file_path = os.path.join(portage_dir, filename)
    with open(config_file_path, "w") as f:
        for line in config_entries:
            f.write(line + "\n")

@root_function
def extract(tarball: str, directory: str):
    """Extracts an .xz tarball as root to preserve special files and ownership."""
    import tarfile
    with tarfile.open(tarball, mode='r:xz') as tar:
        total_size = sum(member.size for member in tar.getmembers())
        extracted_size = 0
        for member in tar.getmembers():
            tar.extract(member, path=directory)
            extracted_size += member.size
            progress = extracted_size / total_size if total_size else 0
            print(f"PROGRESS: {progress}") # This print must stay, it is used to receive progress by step implementation.

