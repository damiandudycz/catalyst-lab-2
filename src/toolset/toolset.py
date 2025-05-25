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
from .root_helper_client import stall_server
from .hotfix_patching import HotFix, apply_patch_and_store_for_isolated_system
from .repository import Serializable, Repository

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

    def run_command(self, command: str, parent: ServerCall | None = None, handler: callable | None = None, completion_handler: callable | None = None) -> ServerCall:
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
                    completion_handler(result)
            try:
                fake_root = os.path.join(self.work_dir, "fake_root")
                return _start_toolset_command._async_raw(
                    handler=handler,
                    # Wraps completion block to set in_use flag additionally after it's done
                    completion_handler=lambda x: on_complete(completion_handler, x),
                    parent=parent,
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

    def analyze(self, parent: ServerCall | None = None) -> bool:
        """Performs various sanity checks on toolset and stores gathered results."""
        """Returns true if all checks succeeded, even if version is not found."""
        checks_succeded = True
        for app in ToolsetApplication.ALL:
            if parent is not None and parent.terminated:
                checks_succeded = False
                break
            try:
                self._perform_app_installed_version_check(app=app, parent=parent)
            except Exception as e:
                print(f"Error in installed version check: {e}")
                checks_succeeded = False
            try:
                self._perform_app_additional_checks(app=app, parent=parent)
            except Exception as e:
                print(f"Error in additional checks: {e}")
                checks_succeeded = False
        Repository.TOOLSETS.save() # Make sure changes are saved in repository.
        return checks_succeded

    def _perform_app_installed_version_check(self, app: ToolsetApplication, parent: ServerCall | None = None):
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
            parent=parent,
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

    def _perform_app_additional_checks(self, app: ToolsetApplication, parent: ServerCall | None = None):
        if app.toolset_additional_analysis:
            app.toolset_additional_analysis(app=app, toolset=self, parent=parent)

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

# ------------------------------------------------------------------------------
# Toolset installation.
# ------------------------------------------------------------------------------
@final
class ToolsetInstallationEvent(Enum):
    """Events produced by installation."""
    # Instance events:
    STATE_CHANGED = auto()
    PROGRESS_CHANGED = auto()
    # Class events:
    STARTED_INSTALLATIONS_CHANGED = auto()

class ToolsetInstallation:
    """Handles the full toolset installation lifecycle."""

    # Remembers installations started in current app cycle. Never removed, even after finishing.
    started_installations: list[ToolsetInstallation] = []
    event_bus: EventBus[ToolsetInstallationEvent] = EventBus[ToolsetInstallationEvent]()

    def __init__(self, stage_url: ParseResult, allow_binpkgs: bool, apps_selection: list[ToolsetApplicationSelection]):
        self.stage_url = stage_url
        self.allow_binpkgs = allow_binpkgs
        self.apps_selection = apps_selection
        self.process_selected_apps()
        self.steps: list[ToolsetInstallationStep] = []
        self.event_bus: EventBus[ToolsetInstallationEvent] = EventBus[ToolsetInstallationEvent]()
        self.status = ToolsetInstallationStage.SETUP
        self.stall_server_call = None
        self.progress: float = 0.0
        self._setup_steps()

    def process_selected_apps(self):
        """Manage auto_select dependencies."""
        app_selections_by_app = { app_selection.app: app_selection for app_selection in self.apps_selection }
        # Mark all dependencies as selected
        for app_selection in self.apps_selection:
            print(f"{app_selection.app.name} : {app_selection.selected}")
            if app_selection.selected:
                for dep in getattr(app_selection.app, "dependencies", []):
                    if dep in app_selections_by_app:
                        app_selections_by_app[dep] = app_selections_by_app[dep]._replace(selected=True)
        # Remove not selected entries
        self.apps_selection = [sel for sel in self.apps_selection if sel.selected]
        # Sort by dependencies
        sorted_entries: list[ToolsetApplicationSelection] = []
        def process_app_selection(app_selection: ToolsetApplicationSelection):
            for dep in getattr(app_selection.app, "dependencies", []):
                process_app_selection(app_selection=app_selections_by_app[dep])
            if not app_selection in sorted_entries:
                sorted_entries.append(app_selection)
        for app_selection in self.apps_selection:
            process_app_selection(app_selection=app_selection)
        self.apps_selection = sorted_entries

    def _setup_steps(self):
        self.steps.append(ToolsetInstallationStepDownload(url=self.stage_url, installer=self))
        self.steps.append(ToolsetInstallationStepExtract(installer=self))
        self.steps.append(ToolsetInstallationStepSpawn(installer=self))
        if self.apps_selection:
            self.steps.append(ToolsetInstallationStepUpdatePortage(installer=self))
        for app_selection in self.apps_selection:
            self.steps.append(ToolsetInstallationStepInstallApp(app_selection=app_selection, installer=self))
        self.steps.append(ToolsetInstallationStepVerify(installer=self))
        self.steps.append(ToolsetInstallationStepCompress(installer=self))
        # Observe all steps progress to calculate overall progress
        for step in self.steps:
            step.event_bus.subscribe(
                ToolsetInstallationStepEvent.PROGRESS_CHANGED,
                self._update_progress
            )

    def _update_progress(self, step_progress: float | None):
        self.progress = sum(step.progress or 0 for step in self.steps) / len(self.steps)
        self.event_bus.emit(ToolsetInstallationEvent.PROGRESS_CHANGED, self.progress)

    def name(self) -> str:
        file_path = Path(self.stage_url.path)
        suffixes = file_path.suffixes
        filename_without_extension = file_path.stem
        for suffix in suffixes:
            filename_without_extension = filename_without_extension.rstrip(suffix)
        parts = filename_without_extension.split("-")
        if len(parts) > 2:
            middle_parts = parts[1:]
            installer_name = " ".join(middle_parts)
        else:
            installer_name = filename_without_extension
        return installer_name

    def start(self, parent: ServerCall | None):
        try:
            def stall_server_call_completion_handler(result: ServerResponse):
                if self.status == ToolsetInstallationStage.INSTALL:
                    self.cancel()
            # Note - stall_server_call is additional, there is also parent created when installation is started.
            # This is to keep root access active even a bit longer, to cleanup later.
            self.stall_server_call = stall_server._async_raw(parent=parent, completion_handler=stall_server_call_completion_handler)
            self.status = ToolsetInstallationStage.INSTALL
            self.event_bus.emit(ToolsetInstallationEvent.STATE_CHANGED, self.status)
            ToolsetInstallation.started_installations.append(self)
            ToolsetInstallation.event_bus.emit(ToolsetInstallationEvent.STARTED_INSTALLATIONS_CHANGED, ToolsetInstallation.started_installations)
            self._continue_installation()
        except Exception as e:
            self.cancel()

    def cancel(self):
        self.status = ToolsetInstallationStage.FAILED if self.status == ToolsetInstallationStage.INSTALL else ToolsetInstallationStage.SETUP
        self.event_bus.emit(ToolsetInstallationEvent.STATE_CHANGED, self.status)
        running_step = next((step for step in self.steps if step.state == ToolsetInstallationStepState.IN_PROGRESS), None)
        if running_step:
            running_step.cancel()
        self._cleanup()

    def clean_from_started_installations(self):
        if self.status == ToolsetInstallationStage.COMPLETED or self.status == ToolsetInstallationStage.FAILED or self.status == ToolsetInstallationStage.SETUP:
            if self in ToolsetInstallation.started_installations:
                ToolsetInstallation.started_installations.remove(self)
                ToolsetInstallation.event_bus.emit(ToolsetInstallationEvent.STARTED_INSTALLATIONS_CHANGED, ToolsetInstallation.started_installations)

    def _cleanup(self):
        def worker():
            for step in reversed(self.steps): # Cleanup in reverse order
                step.cleanup()
            # Stop stalling root server after cleaning is done
            if self.stall_server_call:
                self.stall_server_call.cancel()
        # Run cleaning on new thread, not to block main UI
        threading.Thread(target=worker).start()

    def _continue_installation(self):
        if self.status == ToolsetInstallationStage.COMPLETED or self.status == ToolsetInstallationStage.FAILED or self.status == ToolsetInstallationStage.SETUP:
            return # Prevents displaying multiple failure messages in some cases.
        next_step = next((step for step in self.steps if step.state == ToolsetInstallationStepState.SCHEDULED), None)
        failed_step = next((step for step in self.steps if step.state == ToolsetInstallationStepState.FAILED), None)
        if failed_step:
            self.status = ToolsetInstallationStage.FAILED
            self.event_bus.emit(ToolsetInstallationEvent.STATE_CHANGED, self.status)
            self._cleanup()
            if self.stall_server_call:
                self.stall_server_call.cancel()
        elif next_step:
            next_step_thread = threading.Thread(target=next_step.start)
            next_step_thread.start()
        else:
            self.status = ToolsetInstallationStage.COMPLETED
            self.event_bus.emit(ToolsetInstallationEvent.STATE_CHANGED, self.status)
            self._cleanup()
            ToolsetInstallation.started_installations.remove(self)
            ToolsetInstallation.event_bus.emit(ToolsetInstallationEvent.STARTED_INSTALLATIONS_CHANGED, ToolsetInstallation.started_installations)
            Repository.TOOLSETS.value.append(self.final_toolset)

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
    versions: Tuple[Version, ...] = field(default_factory=tuple)
    dependencies: Tuple[ToolsetApplication, ...] = field(default_factory=tuple)
    auto_select: bool = False # Automatically select / deselect for apps that depends on this one.
    toolset_additional_analysis: Callable[[ToolsetApplication,Toolset,ServerCall|None],None] | None = None # Additional analysis for toolset
    def __post_init__(self):
        # Automatically add new instances to ToolsetApplication.ALL
        ToolsetApplication.ALL.append(self)

ToolsetApplicationSelection = namedtuple("ToolsetApplicationSelection", ["app", "version", "selected", "patches"])

@dataclass(frozen=True)
class PortageConfig:
     # eq: { "packages.use": ["Entry1", "Entry2"], "package.accept_keywords": ["Entry1", "Entry2"] }
    directory: str
    entries: Tuple[str, ...] = field(default_factory=tuple)
@dataclass(frozen=True)
class ToolsetApplicationVersion:
    name: str
    config: PortageConfig = field(default_factory=PortageConfig)

ToolsetApplication.CATALYST = ToolsetApplication(
    name="Catalyst", description="Required to build Gentoo stages",
    package="dev-util/catalyst",
    is_recommended=True, is_highly_recommended=True,
    versions=(
        ToolsetApplicationVersion(
            name="Stable",
            config=(
                PortageConfig(directory="package.accept_keywords", entries=("dev-util/catalyst",)),
                PortageConfig(
                    directory="package.use",
                    entries=(
                        ">=sys-apps/util-linux-2.40.4 python",
                        ">=sys-boot/grub-2.12-r6 grub_platforms_efi-64",
                        ">=sys-boot/grub-2.12-r6 grub_platforms_efi-32",
                    )
                ),
            )
        ),
        ToolsetApplicationVersion(
            name="Experimental",
            config=(
                PortageConfig(directory="package.accept_keywords", entries=("dev-util/catalyst **",)),
                PortageConfig(
                    directory="package.use",
                    entries=(
                        ">=sys-apps/util-linux-2.40.4 python",
                        ">=sys-boot/grub-2.12-r6 grub_platforms_efi-64",
                        ">=sys-boot/grub-2.12-r6 grub_platforms_efi-32",
                    )
                ),
            )
        ),
    )
)
ToolsetApplication.LINUX_HEADERS = ToolsetApplication(
    name="Linux headers", description="Needed for qemu/cmake",
    package="sys-kernel/linux-headers",
    auto_select=True,
    versions=(
            ToolsetApplicationVersion(
                name="Stable",
                config=None
            ),
        ),
)
def toolset_additional_analysis_qemu(app: ToolsetApplication, toolset: Toolset, parent: ServerCall | None = None):
    bin_directory = Path(toolset.toolset_root()) / "bin"
    qemu_systems = Emulation.get_all_qemu_systems()
    found_qemu_binaries = []
    for qemu_binary in qemu_systems:
        binary_path = bin_directory / qemu_binary
        if binary_path.is_file():
            found_qemu_binaries.append(qemu_binary)
    toolset.metadata[app.package] = {
        "interpreters": found_qemu_binaries
    }

ToolsetApplication.QEMU = ToolsetApplication(
    name="Qemu", description="Allows building stages for different architectures",
    package="app-emulation/qemu",
    is_recommended=True,
    versions=(
        ToolsetApplicationVersion(
            name="Stable",
            config=(
                PortageConfig(
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
            )
        ),
        ToolsetApplicationVersion(
            name="Experimental",
            config=(
                PortageConfig(directory="package.accept_keywords", entries=("app-emulation/qemu **",)),
                PortageConfig(
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
            )
        ),
    ),
    dependencies=(ToolsetApplication.LINUX_HEADERS,),
    toolset_additional_analysis=toolset_additional_analysis_qemu
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
    def __init__(self, name: str, description: str, installer: ToolsetInstallation):
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
        if self.server_call:
            self.server_call.cancel()
            if self.server_call.thread:
                self.server_call.thread.join()
            self.server_call = None
    def cleanup(self) -> bool:
        """Returns true if cleanup was needed and was started."""
        if self.state == ToolsetInstallationStepState.SCHEDULED:
            return False # No cleaning needed if job didn't start.
        self.cancel()
        print(f"::: Clean {self.name}")
        return True
    def complete(self, state: ToolsetInstallationStepState):
        """Call this when step finishes."""
        if self._cancel_event.is_set():
            return
        self._update_state(state=state)
        if self.state == ToolsetInstallationStepState.COMPLETED:
           self._update_progress(1.0)
        # Continue installation
        GLib.idle_add(self.installer._continue_installation)
    def _update_state(self, state: ToolsetInstallationStepState):
        self.state = state
        self.event_bus.emit(ToolsetInstallationStepEvent.STATE_CHANGED, state)
    def _update_progress(self, progress: float | None):
        self.progress = progress
        self.event_bus.emit(ToolsetInstallationStepEvent.PROGRESS_CHANGED, progress)
    def run_command_in_toolset(self, command: str, progress_handler: Callable[[str], float | None] | None = None) -> bool:
        try:
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
                parent=self.installer.stall_server_call,
                handler=output_handler if progress_handler is not None else None,
                completion_handler=completion_handler
            )
            self.server_call.thread.join()
            done_event.wait()
            self.server_call = None
            return return_value
        except Exception as e:
            print(f"Error synchronizing portage: {e}")
            self.complete(ToolsetInstallationStepState.FAILED)
            return False

# Steps implementations:

class ToolsetInstallationStepDownload(ToolsetInstallationStep):
    def __init__(self, url: ParseResult, installer: ToolsetInstallation):
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
    def __init__(self, installer: ToolsetInstallation):
        super().__init__(name="Extract stage tarball", description="Extracts Gentoo stage tarball to work directory", installer=installer)
    def start(self):
        super().start()
        try:
            self.installer.tmp_stage_extract_dir = create_temp_workdir(prefix="gentoo_stage_extract_")
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
                parent=self.installer.stall_server_call,
                handler=output_handler,
                completion_handler=completion_handler,
                tarball=self.installer.tmp_stage_file.name,
                directory=self.installer.tmp_stage_extract_dir
            )
            self.server_call.thread.join()
            done_event.wait()
            if not self._cancel_event.is_set():
                self.server_call = None
                self.complete(ToolsetInstallationStepState.COMPLETED if return_value else ToolsetInstallationStepState.FAILED)
        except Exception as e:
            print(f"Error extracting stage tarball: {e}")
            self.complete(ToolsetInstallationStepState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        if hasattr(self.installer, "tmp_stage_extract_dir") and self.installer.tmp_stage_extract_dir:
            delete_temp_workdir(self.installer.tmp_stage_extract_dir)
            return True
        return False

class ToolsetInstallationStepSpawn(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetInstallation):
        super().__init__(name="Create environment", description="Prepares Gentoo environment for work", installer=installer)
    def start(self):
        super().start()
        try:
            toolset_name = self.installer.name()
            self.installer.tmp_toolset = Toolset(ToolsetEnv.EXTERNAL, uuid.uuid4(), toolset_name, squashfs_binding_dir=self.installer.tmp_stage_extract_dir)
            self.installer.tmp_toolset.spawn(store_changes=True)
            commands = [
                "env-update && source /etc/profile",
                "getuto"
            ]
            for i, command in enumerate(commands):
                if self._cancel_event.is_set():
                    return
                result = self.run_command_in_toolset(command=command)
                self._update_progress((i + 1) / len(commands))
                if not result:
                    self.complete(ToolsetInstallationStepState.FAILED)
                    return
            self.complete(ToolsetInstallationStepState.COMPLETED)
        except Exception as e:
            print(f"Error spawning temporary toolset: {e}")
            self.complete(ToolsetInstallationStepState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        if self.installer.tmp_toolset and self.installer.tmp_toolset.spawned:
            self.installer.tmp_toolset.unspawn()
            return True
        return False

class ToolsetInstallationStepUpdatePortage(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetInstallation):
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

class ToolsetInstallationStepInstallApp(ToolsetInstallationStep):
    def __init__(self, app_selection: ToolsetApplicationSelection, installer: ToolsetInstallation):
        super().__init__(name=f"Install {app_selection.app.name}", description=f"Emerges {app_selection.app.package} package", installer=installer)
        self.app_selection = app_selection
    def start(self):
        super().start()
        try:
            def progress_handler(output_line: str) -> float or None:
                pattern = r"^>>> Completed \((\d+) of (\d+)\)"
                match = re.match(pattern, output_line)
                if match:
                    n, m = map(int, match.groups())
                    return n / m
            if self.app_selection.version.config:
                for config in self.app_selection.version.config:
                    if self._cancel_event.is_set():
                        return
                    insert_portage_config(config_dir=config.directory, config_entries=config.entries, app_name=self.app_selection.app.name, toolset_root=self.installer.tmp_toolset.toolset_root())
            for patch_file in self.app_selection.patches:
                file_input_stream = patch_file.read()
                file_info = file_input_stream.query_info("standard::size", None)
                file_size = file_info.get_size()
                patch_content = file_input_stream.read_bytes(file_size, None).get_data().decode()
                insert_portage_patch(patch_content=patch_content, patch_filename=patch_file.get_basename(), app_package=self.app_selection.app.package, toolset_root=self.installer.tmp_toolset.toolset_root())
            flags = "--getbinpkg --deep --update --changed-use" if self.installer.allow_binpkgs else "--deep --update --changed-use"
            result = self.run_command_in_toolset(command=f"emerge {flags} {self.app_selection.app.package}", progress_handler=progress_handler)
            self.complete(ToolsetInstallationStepState.COMPLETED if result else ToolsetInstallationStepState.FAILED)
        except Exception as e:
            print(f"Error during app installation: {e}")
            self.complete(ToolsetInstallationStepState.FAILED)

class ToolsetInstallationStepVerify(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetInstallation):
        super().__init__(name="Analyze toolset", description="Collects information about toolset", installer=installer)
    def start(self):
        super().start()
        try:
            analysis_result = self.installer.tmp_toolset.analyze(parent=self.installer.stall_server_call)
            self.installer.tmp_toolset.unspawn()
            self.complete(ToolsetInstallationStepState.COMPLETED if analysis_result else ToolsetInstallationStepState.FAILED)
        except Exception as e:
            print(f"Error during toolset verification: {e}")
            self.complete(ToolsetInstallationStepState.FAILED)

class ToolsetInstallationStepCompress(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetInstallation):
        super().__init__(name="Compress", description="Compresses toolset into .squashfs file", installer=installer)
    def start(self):
        super().start()
        try:
            self.installer.tmp_toolset_squashfs_dir = create_temp_workdir(prefix="gentoo_toolset_squashfs_")
            self.installer.tmp_toolset_squashfs_file = os.path.join(self.installer.tmp_toolset_squashfs_dir, "toolset.squashfs")
            self.installer.squashfs_process = create_squashfs(source_directory=self.installer.tmp_stage_extract_dir, output_file=self.installer.tmp_toolset_squashfs_file)
            for line in self.installer.squashfs_process.stdout:
                line = line.strip()
                if line.isdigit():
                    percent = int(line)
                    self._update_progress(percent / 100.0)
            self.installer.squashfs_process.wait()
            self.installer.squashfs_process = None
            def sanitize_filename_linux(name: str) -> str:
                return name.replace('/', '_').replace('\0', '_')
            random_id = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
            file_name = f"{sanitize_filename_linux(self.installer.name())}_{random_id}.squashfs"
            file_path = os.path.join(os.path.realpath(os.path.expanduser(Repository.SETTINGS.value.toolsets_location)), file_name)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            shutil.move(self.installer.tmp_toolset_squashfs_file, file_path)
            self.installer.final_toolset = self.installer.tmp_toolset
            self.installer.final_toolset.squashfs_file = file_path
            self.complete(ToolsetInstallationStepState.COMPLETED)
        except Exception as e:
            print(f"Error during toolset compression: {e}")
            self.complete(ToolsetInstallationStepState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        if self.state != ToolsetInstallationStepState.COMPLETED and self.installer.tmp_toolset_squashfs_file and os.path.isfile(self.installer.tmp_toolset_squashfs_file):
            os.remove(self.installer.tmp_toolset_squashfs_file)
        if self.installer.tmp_toolset_squashfs_dir:
            delete_temp_workdir(path=self.installer.tmp_toolset_squashfs_dir)
    def cancel(self):
        super().cancel()
        proc = self.installer.squashfs_process
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=3)
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        self.installer.squashfs_process = None

# ------------------------------------------------------------------------------
# Helper functions:

@root_function
def create_temp_workdir(prefix: str) -> str:
    """Creates temp directory in /var/tmp/catalystlab, owned by the user."""
    import tempfile
    import os
    base_dir = "/var/tmp/catalystlab"
    os.makedirs(base_dir, exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix=prefix, dir=base_dir)
    os.chown(temp_dir, RootHelperServer.shared().uid, RootHelperServer.shared().uid)
    return temp_dir

@root_function
def delete_temp_workdir(path: str) -> bool:
    import os
    import shutil
    try:
        resolved_path = os.path.realpath(path)
        # Ensure we're operating strictly inside the expected directory
        if not (resolved_path.startswith("/var/tmp/catalystlab/") and resolved_path != "/var/tmp/catalystlab"):
            raise ValueError(f"Refusing to delete path outside /var/tmp/catalystlab: {resolved_path}")
        if not os.path.isdir(resolved_path):
            raise FileNotFoundError(f"Path does not exist or is not a directory: {resolved_path}")
        # TODO: Detect if path contains any bindings and raise if it does. Note: os.path.ismount will probably not work for this.
        shutil.rmtree(path)
        print(f"Successfully deleted the directory: {path}")
        return True
    except Exception as e:
        print(f"Failed to delete directory {path}: {e}")
        return False

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
def insert_portage_patch(patch_content: str, patch_filename: str, app_package: str, toolset_root: str):
    portage_dir = os.path.join(toolset_root, "etc", "portage", "patches", app_package)
    os.makedirs(portage_dir, exist_ok=True)
    patch_file_path = os.path.join(portage_dir, patch_filename)
    with open(patch_file_path, "w", encoding="utf-8") as f:
        f.write(patch_content)

@root_function
def mount_squashfs(squashfs_path: str) -> str:
    import os
    import subprocess
    """Creates tmp directory and mounts squashfs file to it"""
    mount_point = create_temp_workdir(prefix="toolset_squashfs_mount_")
    subprocess.run(['mount', '-o', 'loop,ro', squashfs_path, mount_point], check=True)
    return mount_point

@root_function
def umount_squashfs(mount_point: str):
    import os
    import subprocess
    subprocess.run(['umount', mount_point], check=True)
    delete_temp_workdir(path=mount_point)

@root_function
def extract(tarball: str, directory: str):
    import tarfile
    import signal
    import threading
    _cancel_event = threading.Event()
    def handle_sigterm(signum, frame):
        _cancel_event.set()
    signal.signal(signal.SIGTERM, handle_sigterm)

    """Extracts an .xz tarball as root to preserve special files and ownership."""
    with tarfile.open(tarball, mode='r:xz') as tar:
        total_size = sum(member.size for member in tar.getmembers())
        extracted_size = 0
        for member in tar.getmembers():
            if _cancel_event.is_set():
                return
            tar.extract(member, path=directory)
            extracted_size += member.size
            progress = extracted_size / total_size if total_size else 0
            # This print must stay, it is used to receive progress by step implementation.
            print(f"PROGRESS: {progress}", flush=True)

def create_squashfs(source_directory: str, output_file: str) -> subprocess.Popen:
    command = ['mksquashfs', source_directory, output_file, '-quiet', '-percentage']
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    return process

Repository.TOOLSETS = Repository(cls=Toolset, collection=True)
