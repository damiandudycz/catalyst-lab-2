from __future__ import annotations
import os, uuid, shutil, tempfile, threading, re, random, string, requests, time
from gi.repository import GLib
from typing import final, Callable
from pathlib import Path
from enum import Enum, auto
from abc import ABC, abstractmethod
from .root_function import root_function
from .runtime_env import RuntimeEnv
from .architecture import Architecture, Emulation
from .root_helper_server import ServerResponse, ServerResponseStatusCode
from .root_helper_client import AuthorizationKeeper
from .hotfix_patching import apply_patch_and_store_for_isolated_system
from .repository import Repository
from .toolset_application import ToolsetApplication
from .toolset import Toolset, ToolsetEnv
from .helper_functions import create_temp_workdir, delete_temp_workdir, create_squashfs, extract

from .multistage_process import (
    MultiStageProcess, MultiStageProcessStage,
    MultiStageProcessState, MultiStageProcessStageState,
    MultiStageProcessEvent, MultiStageProcessStageEvent,
)

# ------------------------------------------------------------------------------
# Toolset installation.
# ------------------------------------------------------------------------------

class ToolsetInstallation(MultiStageProcess):
    """Handles the full toolset installation lifecycle."""
    def __init__(self, stage_url: ParseResult, allow_binpkgs: bool, apps_selection: list[ToolsetApplicationSelection]):
        self.stage_url = stage_url
        self.allow_binpkgs = allow_binpkgs
        self.apps_selection = apps_selection
        self._process_selected_apps()
        super().__init__(title="Toolset installation")

    def setup_stages(self):
        self.stages.append(ToolsetInstallationStepDownload(url=self.stage_url, multistage_process=self))
        self.stages.append(ToolsetInstallationStepExtract(multistage_process=self))
        self.stages.append(ToolsetInstallationStepSpawn(multistage_process=self))
        if self.apps_selection:
            self.stages.append(ToolsetInstallationStepUpdatePortage(multistage_process=self))
        for app_selection in self.apps_selection:
            self.stages.append(ToolsetInstallationStepInstallApp(app_selection=app_selection, multistage_process=self))
        self.stages.append(ToolsetInstallationStepVerify(multistage_process=self))
        self.stages.append(ToolsetInstallationStepCompress(multistage_process=self))
        super().setup_stages()

    def complete_process(self, success: bool):
        if success:
            Repository.TOOLSETS.value.append(self.toolset)

    def _process_selected_apps(self):
        """Manage auto_select dependencies."""
        app_selections_by_app = { app_selection.app: app_selection for app_selection in self.apps_selection }
        # Mark all dependencies as selected
        for app_selection in self.apps_selection:
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

# ------------------------------------------------------------------------------
# Installation process steps.
# ------------------------------------------------------------------------------

class ToolsetInstallationStep(MultiStageProcessStage):
    def start(self):
        self.server_call = None
        super().start()
    def cancel(self):
        super().cancel()
        if self.server_call:
            self.server_call.cancel()
            if self.server_call.thread:
                self.server_call.thread.join()
            self.server_call = None
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
            self.server_call = self.multistage_process.toolset.run_command(
                command=command,
                handler=output_handler if progress_handler is not None else None,
                completion_handler=completion_handler
            )
            self.server_call.thread.join()
            done_event.wait()
            self.server_call = None
            return return_value
        except Exception as e:
            print(f"Error running toolset command: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
            return False

# Steps implementations:

class ToolsetInstallationStepDownload(ToolsetInstallationStep):
    def __init__(self, url: ParseResult, multistage_process: MultiStageProcess):
        super().__init__(name="Download stage tarball", description="Downloading Gentoo stage tarball", multistage_process=multistage_process)
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
                self.multistage_process.tmp_stage_file = tmp_file
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if self._cancel_event.is_set():
                        return
                    if chunk:
                        tmp_file.write(chunk)
                        downloaded += len(chunk)
                        if total_size:
                            progress = downloaded / total_size
                            self._update_progress(progress)
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during download: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        if hasattr(self.multistage_process, 'tmp_stage_file'):
            try:
                self.multistage_process.tmp_stage_file.close()
                os.remove(self.multistage_process.tmp_stage_file.name)
            except Exception as e:
                print(f"Failed to delete temp file: {e}")
        return True

class ToolsetInstallationStepExtract(ToolsetInstallationStep):
    def __init__(self, multistage_process: MultiStageProcess):
        super().__init__(name="Extract stage tarball", description="Extracts Gentoo stage tarball to work directory", multistage_process=multistage_process)
    def start(self):
        super().start()
        try:
            self.multistage_process.tmp_stage_extract_dir = create_temp_workdir(prefix="gentoo_stage_extract_")
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
                tarball=self.multistage_process.tmp_stage_file.name,
                directory=self.multistage_process.tmp_stage_extract_dir
            )
            self.server_call.thread.join()
            done_event.wait()
            if not self._cancel_event.is_set():
                self.server_call = None
                self.complete(MultiStageProcessStageState.COMPLETED if return_value else MultiStageProcessStageState.FAILED)
        except Exception as e:
            print(f"Error extracting stage tarball: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        if hasattr(self.multistage_process, "tmp_stage_extract_dir") and self.multistage_process.tmp_stage_extract_dir:
            delete_temp_workdir(self.multistage_process.tmp_stage_extract_dir)
            return True
        return False

class ToolsetInstallationStepSpawn(ToolsetInstallationStep):
    def __init__(self, multistage_process: MultiStageProcess):
        super().__init__(name="Create environment", description="Prepares Gentoo environment for work", multistage_process=multistage_process)
    def start(self):
        super().start()
        try:
            toolset_name = self.multistage_process.name()
            self.multistage_process.toolset = Toolset(ToolsetEnv.EXTERNAL, uuid.uuid4(), toolset_name, squashfs_binding_dir=self.multistage_process.tmp_stage_extract_dir)
            now = int(time.time())
            self.multistage_process.toolset.metadata['date_created'] = now
            self.multistage_process.toolset.metadata['date_updated'] = now
            self.multistage_process.toolset.metadata['source'] = self.multistage_process.stage_url.geturl()
            self.multistage_process.toolset.metadata['allow_binpkgs'] = self.multistage_process.allow_binpkgs
            if not self.multistage_process.toolset.reserve():
                raise RuntimeError("Failed to reserve toolset")
            self.multistage_process.toolset.spawn(store_changes=True)
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
                    raise RuntimeError(f"Command {command} failed")
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error spawning temporary toolset: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        if getattr(self.multistage_process, 'toolset', None):
            if self.multistage_process.toolset.spawned:
                self.multistage_process.toolset.unspawn(rebuild_squashfs_if_needed=False)
            self.multistage_process.toolset.release()
            return True
        return False

class ToolsetInstallationStepUpdatePortage(ToolsetInstallationStep):
    def __init__(self, multistage_process: MultiStageProcess):
        super().__init__(name="Synchronize portage", description="Synchronizes portage tree", multistage_process=multistage_process)
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
            self.complete(MultiStageProcessStageState.COMPLETED if result else MultiStageProcessStageState.FAILED)
        except Exception as e:
            print(f"Error synchronizing Portage: {e}")
            self.complete(MultiStageProcessStageState.FAILED)

class ToolsetInstallationStepInstallApp(ToolsetInstallationStep):
    def __init__(self, app_selection: ToolsetApplicationSelection, multistage_process: MultiStageProcess):
        super().__init__(name=f"Install {app_selection.app.name}", description=f"Emerges {app_selection.app.package} package", multistage_process=multistage_process)
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
                    insert_portage_config(config_dir=config.directory, config_entries=config.entries, app_name=self.app_selection.app.name, toolset_root=self.multistage_process.toolset.toolset_root())
            for patch_file in self.app_selection.patches:
                file_input_stream = patch_file.read()
                file_info = file_input_stream.query_info("standard::size", None)
                file_size = file_info.get_size()
                patch_content = file_input_stream.read_bytes(file_size, None).get_data().decode()
                insert_portage_patch(patch_content=patch_content, patch_filename=patch_file.get_basename(), app_package=self.app_selection.app.package, toolset_root=self.multistage_process.toolset.toolset_root())
            flags = "--getbinpkg --deep --update --changed-use" if self.multistage_process.allow_binpkgs else "--deep --update --changed-use"
            result = self.run_command_in_toolset(command=f"emerge {flags} {self.app_selection.app.package}", progress_handler=progress_handler)
            self.complete(MultiStageProcessStageState.COMPLETED if result else MultiStageProcessStageState.FAILED)
        except Exception as e:
            print(f"Error during app installation: {e}")
            self.complete(MultiStageProcessStageState.FAILED)

class ToolsetInstallationStepVerify(ToolsetInstallationStep):
    def __init__(self, multistage_process: MultiStageProcess):
        super().__init__(name="Analyze toolset", description="Collects information about toolset", multistage_process=multistage_process)
    def start(self):
        super().start()
        try:
            analysis_result = self.multistage_process.toolset.analyze()
            self.complete(MultiStageProcessStageState.COMPLETED if analysis_result else MultiStageProcessStageState.FAILED)
        except Exception as e:
            print(f"Error during toolset verification: {e}")
            self.complete(MultiStageProcessStageState.FAILED)

class ToolsetInstallationStepCompress(ToolsetInstallationStep):
    def __init__(self, multistage_process: MultiStageProcess):
        super().__init__(name="Compress", description="Compresses toolset into .squashfs file", multistage_process=multistage_process)
        self.squashfs_process = None
    def start(self):
        super().start()
        try:
            self.toolset_squashfs_dir = create_temp_workdir(prefix="gentoo_toolset_squashfs_")
            self.toolset_squashfs_file = os.path.join(self.toolset_squashfs_dir, "toolset.squashfs")
            self.squashfs_process = create_squashfs(source_directory=self.multistage_process.toolset.toolset_root(), output_file=self.toolset_squashfs_file)
            for line in self.squashfs_process.stdout:
                line = line.strip()
                if line.isdigit():
                    percent = int(line)
                    self._update_progress(percent / 100.0)
            self.squashfs_process.wait()
            self.squashfs_process = None
            def sanitize_filename_linux(name: str) -> str:
                return name.replace('/', '_').replace('\0', '_')
            random_id = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
            file_name = f"{sanitize_filename_linux(self.multistage_process.name())}_{random_id}.squashfs"
            file_path = os.path.join(os.path.realpath(os.path.expanduser(Repository.SETTINGS.value.toolsets_location)), file_name)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            shutil.move(self.toolset_squashfs_file, file_path)
            self.multistage_process.toolset.unspawn(rebuild_squashfs_if_needed=False) # Need to unspawn now, to prevent issues with unmounting after squashfs_file was set
            self.multistage_process.toolset.squashfs_file = file_path
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during toolset compression: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        if self.state != MultiStageProcessStageState.COMPLETED and self.toolset_squashfs_file and os.path.isfile(self.toolset_squashfs_file):
            os.remove(self.toolset_squashfs_file)
        if self.toolset_squashfs_dir:
            delete_temp_workdir(path=self.toolset_squashfs_dir)
    def cancel(self):
        super().cancel()
        proc = self.squashfs_process
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=3)
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        self.squashfs_process = None

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

