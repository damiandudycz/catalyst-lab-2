from __future__ import annotations
import os, uuid, shutil, tempfile, threading, stat, time, subprocess, requests
import tarfile, re, random, string
from gi.repository import Gtk, GLib, Adw
from typing import final, ClassVar, Dict, Any
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from collections import namedtuple
from abc import ABC, abstractmethod
from urllib.parse import ParseResult
from multiprocessing import Event
from collections import namedtuple
from .root_function import root_function
from .runtime_env import RuntimeEnv
from .toolset_env_builder import ToolsetEnvBuilder
from .architecture import Architecture, Emulation
from .event_bus import EventBus
from .root_helper_server import ServerResponse, ServerResponseStatusCode
from .root_helper_client import AuthorizationKeeper
from .hotfix_patching import HotFix, apply_patch_and_store_for_isolated_system
from .repository import Serializable, Repository
from .toolset_application import ToolsetApplication, ToolsetApplicationSelection
from .helper_functions import create_temp_workdir, delete_temp_workdir, mount_squashfs, umount_squashfs

class ToolsetEvents(Enum):
    SPAWNED_CHANGED = auto()
    IN_USE_CHANGED = auto()

@final
class Toolset(Serializable):
    """Class containing details of the Toolset instances."""
    """Only metadata, no functionalities."""
    """Functionalities are handled by ToolsetContainer."""
    def __init__(self, env: ToolsetEnv, uuid: UUID, name: str, apps: dict[str, str] = {}, metadata: dict[str, Any] = {}, squashfs_binding_dir: str | None = None, **kwargs):
        self.uuid = uuid
        self.env = env
        self.name = name
        self.apps = apps
        self.metadata = metadata
        self.squashfs_binding_dir = squashfs_binding_dir # Directory used as toolset_root, mounted when settin up or spawning.
        match env:
            case ToolsetEnv.SYSTEM:
                pass
            case ToolsetEnv.EXTERNAL:
                self.squashfs_file = kwargs.get("squashfs_file")
                if not isinstance(self.squashfs_file, str) and not self.squashfs_binding_dir:
                    raise ValueError("EXTERNAL requires a 'squashfs_file' or a 'squashfs_binding_dir'")
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
        self.event_bus = EventBus[ToolsetEvents]()

    @classmethod
    def init_from(cls, data: dict) -> Toolset:
        try:
            uuid_value = uuid.UUID(data["uuid"])
            env = ToolsetEnv[data["env"]]
            name = str(data["name"])
            apps = data.get("apps", {})
            metadata = data.get("metadata", {})
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
        return cls(env, uuid_value, name, apps, metadata, None, **kwargs)

    def serialize(self) -> dict:
        data = {
            "uuid": str(self.uuid),
            "env": self.env.name,
            "name": self.name,
            "apps": self.apps,
            "metadata": self.metadata
        }
        if self.env == ToolsetEnv.EXTERNAL:
            data["squashfs_file"] = self.squashfs_file
        return data

    @staticmethod
    def create_system() -> Toolset:
        """Create a Toolset with the SYSTEM environment."""
        return Toolset(ToolsetEnv.SYSTEM, uuid.uuid4(), "Host system")

    @staticmethod
    def create_external(squashfs_file: str, name: str) -> Toolset:
        """Create a Toolset with the EXTERNAL environment and a specified squashfs file."""
        return Toolset(ToolsetEnv.EXTERNAL, uuid.uuid4(), name, squashfs_file=squashfs_file)

    def is_allowed_in_current_host() -> bool:
        return self.env.is_running_in_gentoo_host()

    def toolset_root(self) -> str | None:
        match self.env:
            case ToolsetEnv.SYSTEM:
                return "/"
            case ToolsetEnv.EXTERNAL:
                return self.squashfs_binding_dir

    # --------------------------------------------------------------------------
    # Spawning cycle:

    def spawn(self, store_changes: bool = False, hot_fixes: list[HotFix] | None = None, additional_bindings: list[BindMount] | None = None):
        """Prepare /tmp folders for bwrap calls."""
        with self.access_lock:
            if self.spawned:
                raise RuntimeError(f"Toolset {self} already spawned.")

            # Prepare /tmp directories and bind_options
            runtime_env = RuntimeEnv.current()

            if self.squashfs_file and store_changes:
                raise RuntimeError("Mounting SquashFS toolsets is currently not supported.")

            # Create squashfs mounting if needed.
            if self.squashfs_file:
                self.squashfs_binding_dir = mount_squashfs(squashfs_path=self.squashfs_file)

            resolved_toolset_root = str(Path(self.toolset_root()).resolve())
            if resolved_toolset_root == "/" and store_changes:
                raise RuntimeError("Cannot use store_changes with host toolset")
            if not os.path.isdir(resolved_toolset_root):
                raise RuntimeError(f"Toolset root directory not found: {resolved_toolset_root}")

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
                # Work/tmp/cache directories that should always be stored in temporary directory, not in the real toolset.
                BindMount(mount_path="/tmp", create_if_missing=True),
                BindMount(mount_path="/var/tmp", create_if_missing=True),
                BindMount(mount_path="/var/cache", create_if_missing=True),
                BindMount(mount_path="/var/db/repos", create_if_missing=True),
            ]
            # All bindings.
            bindings = ( _system_bindings + _config_bindings + _devices_bindings + _working_bindings + (additional_bindings or []) )

            # Map bindings using toolset_path to host_path.
            for bind in bindings:
                if bind.host_path and bind.toolset_path:
                    raise ValueError(f"BindMount for mount_path '{bind.mount_path}' has both host_path and toolset_path set. Only one is allowed.")
                if bind.toolset_path:
                    bind.host_path = os.path.join(resolved_toolset_root, bind.toolset_path.lstrip("/"))
                # Resolve host_path.
                if bind.host_path:
                    bind.host_path = os.path.expanduser(bind.host_path)

            bind_options = []
            work_dir: str | None = None
            try:
                OverlayPaths = namedtuple("OverlayPaths", ["upper", "work"])
                work_dir = create_temp_workdir(prefix="gentoo_toolset_spawn_")

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
                        # Create in host if store_changes is set.
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
                error = e
                try:
                    if work_dir:
                        delete_temp_workdir(path=work_dir)
                except Exception as e2:
                    error = ExceptionGroup("Multiple errors spawning environment", [error, e2])
                raise error

            self.work_dir = work_dir
            self.hot_fixes = hot_fixes
            self.additional_bindings = additional_bindings
            self.store_changes = store_changes
            self.bind_options = bind_options
            self.spawned = True
            self.event_bus.emit(ToolsetEvents.SPAWNED_CHANGED, self.spawned)

    def unspawn(self):
        """Clear tmp folders."""
        with self.access_lock:
            if not self.spawned:
                raise RuntimeError(f"Toolset {self} is not spawned.")
            if self.in_use:
                raise RuntimeError(f"Toolset {self} is currently in use.")
            try:
                if self.squashfs_binding_dir and self.squashfs_file: # Only umount if both are set, because if only squashfs_binding_dir is, it means it's beining configured for the first time.
                    umount_squashfs(mount_point=self.squashfs_binding_dir)
                if self.work_dir:
                    delete_temp_workdir(path=self.work_dir)
            except Exception as e:
                print(f"Error deleting toolset work_dir: {e}")
                raise e
            finally:
                # Reset spawned settings:
                self.squashfs_binding_dir = None
                self.work_dir = None
                self.hot_fixes = None
                self.additional_bindings = None
                self.store_changes = False
                self.bind_options = None
                self.spawned = False
                self.event_bus.emit(ToolsetEvents.SPAWNED_CHANGED, self.spawned)

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
            self.event_bus.emit(ToolsetEvents.IN_USE_CHANGED, self.in_use)

            def on_complete(completion_handler: callable | None, result: ServerResponse):
                with self.access_lock:
                    self.in_use = False
                    self.event_bus.emit(ToolsetEvents.IN_USE_CHANGED, self.in_use)
                if completion_handler:
                    try:
                        completion_handler(result)
                    except Exception as e:
                        print(f"Completion handler raised exception: {e}")
            try:
                fake_root = os.path.join(self.work_dir, "fake_root")
                return _start_toolset_command._async_raw(
                    handler=handler,
                    # Wraps completion block to set in_use flag additionally after it's done
                    completion_handler=lambda x: on_complete(completion_handler, x),
                    work_dir=self.work_dir,
                    fake_root=fake_root,
                    bind_options=self.bind_options,
                    command_to_run=command
                )
            except Exception as e:
                print(f"Failed to execute command: {e}")
                self.in_use = False
                self.event_bus.emit(ToolsetEvents.IN_USE_CHANGED, self.in_use)
                raise e

    # --------------------------------------------------------------------------
    # Managing installed apps:

    def get_installed_app_version(self, app: ToolsetApplication) -> str | None:
        return self.apps.get(app.package, None)

    def analyze(self) -> bool:
        """Performs various sanity checks on toolset and stores gathered results."""
        """Returns true if all checks succeeded, even if version is not found."""
        checks_succeded = True
        for app in ToolsetApplication.ALL:
            try:
                self._perform_app_installed_version_check(app=app)
            except Exception as e:
                print(f"Error in installed version check: {e}")
                checks_succeeded = False
            try:
                self._perform_app_additional_checks(app=app)
            except Exception as e:
                print(f"Error in additional checks: {e}")
                checks_succeeded = False
        Repository.TOOLSETS.save() # Make sure changes are saved in repository.
        return checks_succeded

    def _perform_app_installed_version_check(self, app: ToolsetApplication):
        """Checks the version of package installed in toolset."""
        """Stores the value in toolset metadata."""
        """If app is not found, stores none for this app in toolset metadata."""
        """If version check fails, throws an exception."""
        version_value = None
        version_response_status = ServerResponseStatusCode.OK
        done_event = threading.Event()
        def completion_handler(response: ServerResponse):
            nonlocal version_response_status
            version_response_status = response.code
            done_event.set()
        def output_handler(output_line: str):
            nonlocal version_value
            def is_valid_package_version(version: str) -> bool:
                gentoo_version_regex = re.compile(
                    r"""^
                    \d+(\.\d+)*                  # version number: 1, 1.2, 1.2.3, etc.
                    (_(alpha|beta|pre|rc|p)\d*)? # optional suffix: _alpha, _beta2, _p20230520, etc.
                    (-r\d+)?                     # optional revision: -r1
                    $""",
                    re.VERBOSE
                )
                return bool(gentoo_version_regex.match(version))
            if output_line and is_valid_package_version(output_line):
                version_value = output_line
        server_call = self.run_command(
            command=(
                f"match=$(ls -d /var/db/pkg/{app.package}-* 2>/dev/null | head -n1); "
                f"if [[ -n \"$match\" ]]; then basename \"$match\" | sed -E \"s/^$(basename \"{app.package}\")-//\"; fi"
            ),
            handler=output_handler,
            completion_handler=completion_handler
        )
        server_call.thread.join()
        done_event.wait()
        if version_response_status != ServerResponseStatusCode.OK:
            self.apps.pop(app.package, None)
            raise RuntimeError(f"Failed to get package version. App: {app.package}, Code: {version_response_status.name}")
        if version_value is not None:
            self.apps[app.package] = version_value
        else:
            self.apps.pop(app.package, None)

    def _perform_app_additional_checks(self, app: ToolsetApplication):
        if app.toolset_additional_analysis:
            app.toolset_additional_analysis(app=app, toolset=self)

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
    run_dir = RootHelperServer.get_runtime_dir(uid=RootHelperServer.shared().uid, runtime_env_name="CL_SERVER_RUNTIME_DIR")
    bwrap_path = os.path.join(run_dir, "bwrap")
    cmd_bwrap = (
        f"{bwrap_path} "
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

