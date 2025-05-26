from __future__ import annotations
import os, uuid, shutil, tempfile, threading, re, random, string, requests
from gi.repository import GLib
from typing import final, Callable
from pathlib import Path
from enum import Enum, auto
from abc import ABC, abstractmethod
from .root_function import root_function
from .runtime_env import RuntimeEnv
from .architecture import Architecture, Emulation
from .event_bus import EventBus
from .root_helper_server import ServerResponse, ServerResponseStatusCode
from .root_helper_client import AuthorizationKeeper
from .hotfix_patching import apply_patch_and_store_for_isolated_system
from .repository import Repository
from .toolset_application import ToolsetApplication
from .helper_functions import create_temp_workdir, delete_temp_workdir, create_squashfs, extract

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
        self.progress: float = 0.0
        self.authorization_keeper: AuthorizationKeeper | None = None
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
            middle_parts = parts[1:-1]
            installer_name = " ".join(middle_parts)
        else:
            installer_name = filename_without_extension
        return installer_name

    def start(self, authorization_keeper: AuthorizationKeeper):
        try:
            authorization_keeper.retain()
            self.authorization_keeper = authorization_keeper
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
            self.authorization_keeper.release()
            self.authorization_keeper = None
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
        from .toolset import Toolset, ToolsetEnv
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
        if getattr(self.installer, 'tmp_toolset', None) is not None and self.installer.tmp_toolset.spawned:
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
            analysis_result = self.installer.tmp_toolset.analyze()
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

