from __future__ import annotations
import json, os, re, threading, shutil
from datetime import datetime, timezone
from gi.repository import GLib
from .multistage_process import (
    MultiStageProcess, MultiStageProcessStage, MultiStageProcessState, MultiStageProcessStageState,
    MultiStageProcessEvent, MultiStageProcessStageEvent
)
from .root_function import root_function
from .repository import Repository
from .root_helper_server import ServerResponse, ServerResponseStatusCode
from .toolset import BindMount
from .toolset_env_builder import ToolsetEnvBuilder
from .project_stage_arguments import StageArgumentDetails
from .project_stage_cache import CACHE_ARGUMENTS, stage_cache_path
from .rootless import extracted_squashfs, remove_stale_sessions, distfiles_directory
from .project_build_rootless import stage_build_session_script, save_interrupted_caches, CATALYST_WRAPPER
from .project_build import StageBuild, StageBuildStatus, StageBuildPlan, project_builds_directory, stage_builds_directory
from .project_build_spec import (
    StageSpecContext, generate_stage_spec, generate_portage_confdir, generate_root_overlay, seed_name_prefix, select_seed_url, snapshot_treeish
)

# Paths used by catalyst inside toolset.
CATALYST_BUILDS_PATH = "/var/tmp/catalyst/builds"
CATALYST_SNAPSHOTS_PATH = "/var/tmp/catalyst/snapshots"
# Archive extensions catalyst can produce, used to find built stage file.
STAGE_ARCHIVE_EXTENSIONS = (".tar.xz", ".tar.bz2", ".tar.gz", ".tar.zst", ".tar", ".squashfs")

# ------------------------------------------------------------------------------
# Build process.
# ------------------------------------------------------------------------------

class ProjectBuild(MultiStageProcess):
    """Builds stages of project selected in build plan, parents first. Failed stage doesn't stop the process,
    only stages depending on it are skipped, so other branches are still built.
    Builds use snapshot selected for build (project snapshot by default), or latest snapshot generated with project
    toolset before building. Selected snapshot can be stored in project too."""

    def __init__(self, project_directory, plan: StageBuildPlan, snapshot=None, fetch_snapshot: bool = False,
                 update_project_snapshot: bool = False):
        self.project_directory = project_directory
        self.plan = plan
        self.snapshot = None if fetch_snapshot else (snapshot or project_directory.get_snapshot()) # Set when fetched.
        self.fetch_snapshot = fetch_snapshot
        self.update_project_snapshot = update_project_snapshot
        self.toolset = project_directory.get_toolset()
        self.timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") # @TIMESTAMP@ for all stages of this build.
        self.builds_directory = project_builds_directory(project_directory)
        self.seed_subpaths: dict = {} # Downloaded seeds of root stages, relative to builds directory.
        self.stage_builds: dict = {} # Builds made in this run, by stage id.
        self.failed_stage_ids: set = set()
        # Builds run without root privileges in user namespace when system supports it, otherwise in toolset spawned
        # by root helper.
        # Builds of toolsets linked to virtual machine run in that machine (always without root privileges).
        self.machine = self.toolset.machine if self.toolset else None
        self.executor = self.toolset.executor if self.toolset else None
        self.rootless_unsupported_reason = self.executor.unsupported_reason() if self.executor and not self.machine else None
        self.rootless = self.machine is not None or self.rootless_unsupported_reason is None
        self.rootless_toolset_path: str | None = None # Extracted toolset and snapshot, set when preparing toolset.
        self.rootless_snapshot_path: str | None = None
        super().__init__(title=f"Building {project_directory.name}")

    def name(self) -> str:
        return self.project_directory.name

    def setup_stages(self):
        if self.fetch_snapshot:
            self.stages.append(ProjectBuildStepFetchSnapshot(multistage_process=self))
        self.stages.append(ProjectBuildStepPrepareToolset(multistage_process=self))
        for stage in self.plan.build_order():
            if self.plan.parent(stage) is None:
                self.stages.append(ProjectBuildStepDownloadSeed(stage=stage, multistage_process=self))
            self.stages.append(ProjectBuildStepBuildStage(stage=stage, multistage_process=self))

    def container_path(self, host_path: str) -> str:
        """Path inside toolset of file in project builds directory. Other paths are mounted at the same location."""
        if host_path != self.builds_directory and not host_path.startswith(self.builds_directory + os.sep):
            return host_path
        return os.path.join(CATALYST_BUILDS_PATH, os.path.relpath(host_path, self.builds_directory))

    def mirrored_paths(self) -> list[str]:
        """Host directories referenced by spec values (@REPO_DIR@, root_overlay, repos, selected cache folders),
        available in toolset at the same paths."""
        project = self.project_directory
        paths = [project.directory_path()]
        if releng_directory := project.get_releng_directory():
            paths.append(releng_directory.directory_path())
        paths += [overlay.directory_path() for overlay in Repository.OverlayDirectory.value]
        paths = [path for path in paths if os.path.isdir(path)]
        # Caches in folders selected by user (automatic ones are in builds directory).
        paths += sorted({
            path for stage in self.plan.build_order() for path in self.stage_cache_paths(stage).values()
            if self.container_path(path) == path
        })
        return paths

    def stage_cache_paths(self, stage) -> dict:
        """Folders of enabled caches of stage, by argument. Automatic caches of machine builds are on disk of machine
        (shared folders can't store owners of files that portage sets), in its cache directory."""
        paths = {
            argument: path for argument in CACHE_ARGUMENTS
            if (path := stage_cache_path(self.project_directory, stage, argument))
        }
        if self.machine:
            # Project id instead of name, paths in spec can't contain spaces.
            project_caches = os.path.join(self.executor.cache_directory(), "Projects", self.project_directory.id.hex)
            paths = {
                argument: os.path.join(project_caches, os.path.relpath(path, self.builds_directory))
                if path.startswith(self.builds_directory + os.sep) else path
                for argument, path in paths.items()
            }
        return paths

    def synced_caches(self, stage) -> list[tuple[str, str]]:
        """Caches of machine build kept in shared folders between builds, as (saved path, path in working space).
        Working space is deleted after build, caches are saved as plain files (shared folders can't store owners)."""
        if not self.machine:
            return []
        caches = [(distfiles_directory(), distfiles_directory(self.executor))]
        used_paths = self.stage_cache_paths(stage)
        for argument in CACHE_ARGUMENTS:
            saved = stage_cache_path(self.project_directory, stage, argument)
            if saved and saved.startswith(self.builds_directory + os.sep) and argument in used_paths:
                caches.append((saved, used_paths[argument]))
        return caches

    def make_directories(self, paths: list[str]):
        """Creates directories, ones in cache or temporary directory of machine are created in machine."""
        machine_paths = [path for path in paths if self.machine and self.executor.owns_path(path)]
        for path in paths:
            if path not in machine_paths:
                os.makedirs(path, exist_ok=True)
        if machine_paths:
            self.executor.filesystem({"mkdir": machine_paths})

    def seed_subpath(self, stage) -> str | None:
        """Seed of stage, relative to builds directory, without extension (as catalyst expects source_subpath)."""
        parent = self.plan.parent(stage)
        if parent is None:
            return self.seed_subpaths.get(stage.id)
        build = self.stage_builds.get(parent.id) or self.plan.entries[parent.id].reused_build
        if build is None or not build.artifact_path:
            return None
        return _strip_archive_extension(os.path.relpath(build.artifact_path, self.builds_directory))

    def is_blocked(self, step: MultiStageProcessStage) -> str | None:
        """Reason why step can't run because something it depends on failed."""
        if isinstance(self.stages[0], ProjectBuildStepPrepareToolset) and self.stages[0].state == MultiStageProcessStageState.FAILED:
            return "Toolset preparation failed"
        stage = getattr(step, "stage", None)
        while stage is not None:
            if stage.id in self.failed_stage_ids:
                return f"{stage.name} failed" if stage is not step.stage else "Seed download failed"
            stage = self.plan.parent(stage)
        return None

    def _continue_process(self):
        """Like base implementation, but failed steps only skip steps that depend on them."""
        if self.status in (MultiStageProcessState.COMPLETED, MultiStageProcessState.FAILED, MultiStageProcessState.SETUP):
            return
        for step in self.stages:
            if step.state == MultiStageProcessStageState.FAILED and (stage := getattr(step, "stage", None)):
                self.failed_stage_ids.add(stage.id)
        for step in self.stages:
            if step.state != MultiStageProcessStageState.SCHEDULED:
                continue
            if reason := self.is_blocked(step):
                step.log(f"Skipped: {reason}")
                step._update_state(MultiStageProcessStageState.FAILED)
                if stage := getattr(step, "stage", None):
                    self.failed_stage_ids.add(stage.id)
                continue
            threading.Thread(target=step.start).start()
            return
        # All steps finished:
        success = not any(step.state == MultiStageProcessStageState.FAILED for step in self.stages)
        self.status = MultiStageProcessState.COMPLETED if success else MultiStageProcessState.FAILED
        self.event_bus.emit(MultiStageProcessEvent.STATE_CHANGED, self.status)
        try:
            self._cleanup()
        except Exception as e:
            print(e)
        finally:
            if success:
                MultiStageProcess.started_processes.remove(self)
            # Failed builds stay in started processes (to see their output), but views showing running builds need
            # to be refreshed in both cases.
            MultiStageProcess.event_bus.emit(
                MultiStageProcessEvent.STARTED_PROCESSES_CHANGED,
                self.__class__,
                MultiStageProcess.get_started_processes_by_class(self.__class__)
            )
            self.complete_process(success=success)

    # Records of stages in this run are saved when it starts, so stages that don't start (skipped after failure of
    # stage they depend on, or cancelled) are listed in builds too.

    def start(self, authorization_keeper=None):
        for order, stage in enumerate(self.plan.build_order()):
            try:
                StageBuild(stage_id=stage.id, stage_name=stage.name, timestamp=self.timestamp, order=order,
                           status=StageBuildStatus.SCHEDULED).save(self.project_directory)
            except OSError as e:
                print(f"Failed to save scheduled build of {stage.name}: {e}")
        super().start(authorization_keeper)

    def cancel(self):
        self.cancelled = True
        super().cancel()

    def store_project_snapshot(self):
        """Selected snapshot becomes snapshot of project."""
        if self.snapshot is None:
            return False
        self.project_directory.initialize_metadata().snapshot_id = self.snapshot.filename
        Repository.ProjectDirectory.save()
        return False

    def complete_process(self, success: bool):
        status = StageBuildStatus.CANCELLED if getattr(self, "cancelled", False) else StageBuildStatus.SKIPPED
        for stage in self.plan.build_order():
            path = os.path.join(stage_builds_directory(self.project_directory, stage.name), self.timestamp)
            try:
                with open(os.path.join(path, StageBuild.METADATA_FILE), encoding="utf-8") as file:
                    build = StageBuild.init_from(json.load(file), path=path)
                if build.status == StageBuildStatus.SCHEDULED:
                    build.status = status
                    build.save(self.project_directory)
            except (OSError, ValueError, KeyError) as e:
                print(f"Failed to update build of {stage.name}: {e}")

def running_project_build(project_directory, timestamp: str | None = None) -> ProjectBuild | None:
    """Build of project that is currently in progress, optionally only build started with given timestamp."""
    return next((
        build for build in MultiStageProcess.get_started_processes_by_class(ProjectBuild)
        if build.project_directory.id == project_directory.id
        and build.status == MultiStageProcessState.IN_PROGRESS
        and (timestamp is None or build.timestamp == timestamp)
    ), None)

# ------------------------------------------------------------------------------
# Build steps.
# ------------------------------------------------------------------------------

class ProjectBuildStep(MultiStageProcessStage):
    def start(self):
        self.server_call = None
        self.namespace_processes = [] # Rootless processes, terminated when cancelled.
        super().start()
    def cancel(self):
        super().cancel()
        for process in getattr(self, "namespace_processes", []):
            process.terminate()
        if self.server_call:
            self.server_call.cancel()
            if self.server_call.thread:
                self.server_call.thread.join()
    def run_command_in_toolset(self, command: str) -> bool:
        """Runs command in toolset, its output is added to step output."""
        result = False
        done_event = threading.Event()
        def completion_handler(response: ServerResponse):
            nonlocal result
            result = response.code == ServerResponseStatusCode.OK
            done_event.set()
        self.log(f"$ {command}")
        self.server_call = self.multistage_process.toolset.run_command(command=command, completion_handler=completion_handler)
        self.server_call.thread.join()
        done_event.wait()
        return result

class ProjectBuildStepFetchSnapshot(ProjectBuildStep):
    """Generates latest snapshot of Gentoo repository with project toolset (like Snapshots section does), and uses it
    in this build. Snapshot is stored with other snapshots, and in project when user chose that."""
    def __init__(self, multistage_process: ProjectBuild):
        super().__init__(name="Get latest snapshot", description="Generates snapshot of Gentoo repository with project toolset", multistage_process=multistage_process)
        self.installation = None
    def start(self):
        super().start()
        try:
            from .snapshot_installation import SnapshotInstallation
            process = self.multistage_process
            toolset = process.toolset
            if toolset is None:
                raise RuntimeError("Project has no toolset")
            if reason := toolset.busy_reason:
                raise RuntimeError(reason)
            installation = SnapshotInstallation(toolset=toolset)
            self.installation = installation
            finished = threading.Event()
            def on_state_changed(state):
                if state in (MultiStageProcessState.COMPLETED, MultiStageProcessState.FAILED):
                    # Snapshot is added to repository (and toolset released) after state changes, on main thread.
                    GLib.idle_add(finished.set)
            # Event bus keeps weak references, handlers are kept by step.
            self._handlers = [on_state_changed]
            installation.event_bus.subscribe(MultiStageProcessEvent.STATE_CHANGED, on_state_changed)
            for stage in installation.stages:
                handler = lambda state, stage=stage: self._stage_changed(stage, state)
                self._handlers.append(handler)
                stage.event_bus.subscribe(MultiStageProcessStageEvent.OUTPUT_LINE_ADDED, self.log)
                stage.event_bus.subscribe(MultiStageProcessStageEvent.STATE_CHANGED, handler)
            installation.start(authorization_keeper=process.authorization_keeper)
            finished.wait()
            if installation.status != MultiStageProcessState.COMPLETED or installation.snapshot is None:
                raise RuntimeError("Failed to generate snapshot")
            process.snapshot = installation.snapshot
            self.log(f"Using snapshot {installation.snapshot.filename} ({installation.snapshot.name})")
            if process.update_project_snapshot:
                GLib.idle_add(process.store_project_snapshot)
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during snapshot generation: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def _stage_changed(self, stage, state):
        if state == MultiStageProcessStageState.IN_PROGRESS:
            self.log(f"--- {stage.name}")
        stages = self.installation.stages if self.installation else []
        done = sum(1 for item in stages if item.state == MultiStageProcessStageState.COMPLETED)
        self._update_progress(done / len(stages) if stages else None)
    def cancel(self):
        super().cancel()
        if self.installation and self.installation.status == MultiStageProcessState.IN_PROGRESS:
            self.installation.cancel()

class ProjectBuildStepPrepareToolset(ProjectBuildStep):
    def __init__(self, multistage_process: ProjectBuild):
        super().__init__(name="Prepare toolset", description="Spawns toolset with builds, snapshots and project directories", multistage_process=multistage_process)
        self.reserved = False
        self.spawned = False
    def start(self):
        super().start()
        try:
            toolset = self.multistage_process.toolset
            if toolset is None:
                raise RuntimeError("Project has no toolset")
            if reason := toolset.busy_reason:
                raise RuntimeError(reason)
            if not toolset.reserve():
                raise RuntimeError(f"Toolset {toolset.name} is used by another operation")
            self.reserved = True
            os.makedirs(self.multistage_process.builds_directory, exist_ok=True)
            if self.multistage_process.rootless:
                self._prepare_rootless()
                self.complete(MultiStageProcessStageState.COMPLETED)
                return
            self.log(f"Building with root privileges: {self.multistage_process.rootless_unsupported_reason}")
            # Catalyst mounts snapshot squashfs with loop device. Loop device nodes are created by kernel in host /dev,
            # so free ones are prepared before spawning and only they are made available in toolset.
            self.loop_devices = prepare_loop_devices()
            for binding in self.required_bindings():
                self.log(f"Binding {binding.host_path} -> {binding.mount_path}")
            toolset.spawn(additional_bindings=self.required_bindings())
            if not toolset.spawned:
                raise RuntimeError("Failed to spawn toolset")
            self.spawned = True
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during '{self.name}': {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def _prepare_rootless(self):
        """Extracts toolset and snapshot (once for every version of their files), used by stage builds."""
        process = self.multistage_process
        executor = process.executor
        if process.machine:
            self.log(f"Building in virtual machine {process.machine.name}, without root privileges")
            process.machine.ensure_running(self.log, self.namespace_processes)
            self.workspace_user = f"Build of {process.project_directory.name}"
            process.machine.acquire_workspace(self.log, self.namespace_processes, user=self.workspace_user)
            self.workspace_acquired = True
        else:
            self.log("Building without root privileges, in user namespace")
        remove_stale_sessions(self.log, self.namespace_processes, executor=executor)
        process.rootless_toolset_path = extracted_squashfs(
            process.toolset.file_path(), "toolsets", self.log, self.namespace_processes, check_path="bin/bash", executor=executor)
        snapshot = process.snapshot
        if snapshot is None:
            raise RuntimeError("Build has no snapshot")
        process.rootless_snapshot_path = extracted_squashfs(
            snapshot.file_path(), "snapshots", self.log, self.namespace_processes, check_path="profiles", executor=executor)
        process.make_directories([distfiles_directory(executor)] + process.mirrored_paths())
    def required_bindings(self) -> list[BindMount]:
        process = self.multistage_process
        bindings = [
            # Catalyst writes builds here and reads seeds from it.
            BindMount(mount_path=CATALYST_BUILDS_PATH, host_path=process.builds_directory, store_changes=True, create_if_missing=True),
            BindMount(mount_path=CATALYST_SNAPSHOTS_PATH, host_path=Repository.Settings.value.snapshots_location, store_changes=True),
        ]
        # Directories referenced by spec values with host paths (@REPO_DIR@, root_overlay, repos, cache folders) are
        # available at the same paths.
        bindings += [BindMount(mount_path=path, host_path=path, store_changes=True, create_if_missing=True) for path in process.mirrored_paths()]
        # Loop devices for mounting snapshot (store_changes gives read-write device access).
        bindings += [BindMount(mount_path=path, host_path=path, store_changes=True) for path in getattr(self, "loop_devices", [])]
        return bindings
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        toolset = self.multistage_process.toolset
        if self.spawned:
            toolset.unspawn(rebuild_squashfs_if_needed=False)
        if getattr(self, "workspace_acquired", False):
            # Working space is deleted with caches changed by cancelled build, they are saved first.
            try:
                save_interrupted_caches(self.multistage_process.executor, self.log)
            except Exception as e:
                self.log(f"Failed to save caches: {e}")
            self.multistage_process.machine.release_workspace(self.log, user=self.workspace_user)
        if self.reserved:
            toolset.release()
        return True

class ProjectBuildStepDownloadSeed(ProjectBuildStep):
    def __init__(self, stage, multistage_process: ProjectBuild):
        super().__init__(name=f"Download seed for {stage.name}", description="Downloads latest stage3 from Gentoo mirrors", multistage_process=multistage_process)
        self.stage = stage
    def start(self):
        super().start()
        try:
            import requests
            process = self.multistage_process
            architecture = process.project_directory.get_architecture()
            prefix = seed_name_prefix(process.project_directory, self.stage)
            self.log(f"Looking for latest {prefix}")
            result = None
            def completion_handler(value):
                nonlocal result
                result = value
            ToolsetEnvBuilder.get_stage3_urls(completion_handler=completion_handler, architecture=architecture)
            if isinstance(result, Exception) or result is None:
                raise RuntimeError(f"Failed to read list of stage3 files: {result}")
            url = select_seed_url(result, prefix)
            if url is None:
                raise RuntimeError(f"No {prefix} found in latest stage3 files")
            filename = os.path.basename(url.path)
            seeds_directory = os.path.join(process.builds_directory, "seeds")
            path = os.path.join(seeds_directory, filename)
            if os.path.isfile(path):
                self.log(f"Using already downloaded {filename}")
            else:
                os.makedirs(seeds_directory, exist_ok=True)
                self.log(f"Downloading {url.geturl()}")
                with requests.get(url.geturl(), stream=True, timeout=60) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("content-length", 0))
                    downloaded = 0
                    with open(path + ".part", "wb") as file:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if self._cancel_event.is_set():
                                raise RuntimeError("Cancelled")
                            file.write(chunk)
                            downloaded += len(chunk)
                            if total:
                                self._update_progress(downloaded / total)
                os.replace(path + ".part", path)
            process.seed_subpaths[self.stage.id] = _strip_archive_extension(os.path.relpath(path, process.builds_directory))
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during '{self.name}': {e}")
            self.complete(MultiStageProcessStageState.FAILED)

class EmergeProgress:
    """Progress of packages built by catalyst, read from its output: emerge prints "(N of M)" for every package.
    Catalyst runs every emerge with run_merge, which first prints the command. Update of seed (stage1 with
    update_seed) is the first emerge after "Updating seed stage...", it has no progress, as it's not part of the
    stage. Stages run several emerges, progress is of the current one (emerges of single package are skipped)."""
    _ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    _COMPLETED = re.compile(r"^>>> Completed(?: binary)? \((\d+) of (\d+)\)")

    def __init__(self):
        self.seed_update = False
        self.seed_update_started = False

    def parse(self, line: str) -> float | None:
        """Progress when line changes it, otherwise None."""
        line = self._ANSI.sub("", line).strip()
        if line.startswith("Updating seed stage"):
            self.seed_update, self.seed_update_started = True, False
        elif line.startswith("emerge ") and self.seed_update:
            if self.seed_update_started:
                self.seed_update = False # Next emerge after the seed update.
            else:
                self.seed_update_started = True
        elif not self.seed_update and (match := self._COMPLETED.match(line)) and int(match.group(2)) > 1:
            # Emerges of single package (eg. baselayout before stage1 packages) would show 100% before the real list.
            return int(match.group(1)) / int(match.group(2))
        return None

class ProjectBuildStepBuildStage(ProjectBuildStep):
    def __init__(self, stage, multistage_process: ProjectBuild):
        super().__init__(name=f"Build {stage.name}", description=f"Builds {stage.target.replace('_', '-')} with catalyst", multistage_process=multistage_process)
        self.stage = stage
        self.build: StageBuild | None = None
        self._log_file = None
        self._emerge_progress = EmergeProgress()
    def log(self, line: str):
        """Output is also saved as build.log in build directory, to keep it after app is closed. Packages built by
        catalyst are shown as progress."""
        super().log(line)
        if (progress := self._emerge_progress.parse(line)) is not None:
            self._update_progress(progress)
        if self.build and self.build.path:
            try:
                if self._log_file is None:
                    self._log_file = open(os.path.join(self.build.path, "build.log"), "a", encoding="utf-8", buffering=1)
                self._log_file.write(line + "\n")
            except OSError as e:
                print(f"Failed to write build log: {e}")
    def complete(self, state: MultiStageProcessStageState):
        super().complete(state) # Can log reason of failure.
        if self._log_file:
            self._log_file.close()
            self._log_file = None
    def start(self):
        super().start()
        process = self.multistage_process
        project = process.project_directory
        try:
            self.build = StageBuild(stage_id=self.stage.id, stage_name=self.stage.name, timestamp=process.timestamp,
                                    order=process.plan.build_order().index(self.stage))
            self.build.save(project)
            # Seed:
            source_subpath = process.seed_subpath(self.stage)
            if source_subpath is None:
                raise RuntimeError("Seed of stage is not available")
            # Portage configuration and spec:
            work_directory = os.path.join(process.builds_directory, "work", process.timestamp, project.sanitized_name_for_name(self.stage.name))
            if os.path.isdir(work_directory):
                shutil.rmtree(work_directory)
            os.makedirs(work_directory)
            portage_path = os.path.join(work_directory, "portage")
            has_confdir = generate_portage_confdir(project, self.stage, portage_path)
            root_overlay_path = os.path.join(work_directory, "root_overlay")
            has_root_overlay = generate_root_overlay(project, self.stage, root_overlay_path)
            cache_paths = process.stage_cache_paths(self.stage)
            process.make_directories(list(cache_paths.values()))
            spec = generate_stage_spec(project, self.stage, StageSpecContext(
                timestamp=process.timestamp,
                source_subpath=source_subpath,
                portage_confdir=process.container_path(portage_path) if has_confdir else None,
                root_overlay=process.container_path(root_overlay_path) if has_root_overlay else None,
                cache_paths={argument: process.container_path(path) for argument, path in cache_paths.items()},
                snapshot=process.snapshot,
            ))
            spec_path = os.path.join(work_directory, "stage.spec")
            with open(spec_path, "w", encoding="utf-8") as file:
                file.write(spec)
            self.log("Spec:")
            for line in spec.splitlines():
                self.log(f"  {line}")
            # Build:
            wrapper_path = None
            if process.rootless:
                wrapper_path = os.path.join(work_directory, "catalyst-wrapper.py")
                with open(wrapper_path, "w", encoding="utf-8") as file:
                    file.write(CATALYST_WRAPPER)
            build_script_path = os.path.join(work_directory, "build.sh")
            with open(build_script_path, "w", encoding="utf-8") as file:
                file.write(_catalyst_build_script(
                    spec_path=process.container_path(spec_path),
                    config_path=process.container_path(os.path.join(work_directory, "catalyst.conf")),
                    caches={_catalyst_cache_options[argument] for argument in cache_paths},
                    wrapper_path=process.container_path(wrapper_path) if wrapper_path else None,
                ))
            if process.rootless:
                if not self._run_rootless(spec=spec, work_directory=work_directory, build_script_path=build_script_path):
                    raise RuntimeError("Catalyst build failed")
            # Command is passed in single quotes, path in double quotes allows spaces in stage name.
            elif not self.run_command_in_toolset(f'bash "{process.container_path(build_script_path)}"'):
                self._run_diagnostics(spec=spec, work_directory=work_directory)
                raise RuntimeError("Catalyst build failed")
            # Move results to stage build directory (catalyst writes them to builds/<rel_type>/):
            values = _spec_values(spec)
            output_name = f"{values['target']}-{values['subarch']}-{values['version_stamp']}"
            output_directory = os.path.join(process.builds_directory, values["rel_type"])
            if process.rootless:
                # Files written by namespace root are already owned by user.
                moved = _move_stage_build_files(source_directory=output_directory, name=output_name, destination_directory=self.build.path)
            else:
                moved = move_stage_build_files(source_directory=output_directory, name=output_name, destination_directory=self.build.path)
            artifact = next((filename for filename in moved if filename.endswith(STAGE_ARCHIVE_EXTENSIONS)), None)
            if artifact is None:
                raise RuntimeError(f"Built stage {output_name} not found in {output_directory}")
            self.log(f"Saved {', '.join(moved)} to {self.build.path}")
            self.build.artifact = artifact
            self.build.status = StageBuildStatus.COMPLETED
            self.build.save(project)
            process.stage_builds[self.stage.id] = self.build
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during '{self.name}': {e}")
            if self.build:
                self.build.status = StageBuildStatus.FAILED
                try:
                    self.build.save(project)
                except Exception as save_error:
                    print(f"Failed to save build status: {save_error}")
            self.complete(MultiStageProcessStageState.FAILED)

    def _run_rootless(self, spec: str, work_directory: str, build_script_path: str) -> bool:
        """Runs build script in toolset root inside user namespace, with diagnostics after failure."""
        process = self.multistage_process
        diagnostics_path = self._write_diagnostics_script(spec=spec, work_directory=work_directory, rootless=True)
        bindings = [
            (process.builds_directory, CATALYST_BUILDS_PATH),
            (process.rootless_snapshot_path, f"{CATALYST_SNAPSHOTS_PATH}/gentoo-{snapshot_treeish(process.project_directory, process.snapshot)}.sqfs"),
            (distfiles_directory(process.executor), "/var/cache/distfiles"),
        ] + [(path, path) for path in process.mirrored_paths()]
        script = stage_build_session_script(
            executor=process.executor,
            toolset_path=process.rootless_toolset_path,
            bindings=bindings,
            command=f'bash "{process.container_path(build_script_path)}"',
            diagnostics_command=f'bash "{process.container_path(diagnostics_path)}"' if diagnostics_path else None,
            synced_caches=process.synced_caches(self.stage),
        )
        self.log(f"$ bash {process.container_path(build_script_path)}")
        return process.executor.run_in_namespace(script, self.log, self.namespace_processes)

    def _run_diagnostics(self, spec: str, work_directory: str):
        """Collects details about chroot left by failed catalyst build. Catalyst hides errors of some scripts
        (eg. stage1 build.py), this runs the same checks with errors visible."""
        if script_path := self._write_diagnostics_script(spec=spec, work_directory=work_directory, rootless=False):
            self.run_command_in_toolset(f'bash "{self.multistage_process.container_path(script_path)}"')

    def _write_diagnostics_script(self, spec: str, work_directory: str, rootless: bool) -> str | None:
        """Writes diagnostics script, returns its path. Rootless builds use extracted snapshot folder instead of
        squashfs file, which is bound instead of loop mounted."""
        try:
            process = self.multistage_process
            values = _spec_values(spec)
            chroot = f"/var/tmp/catalyst/tmp/{values['rel_type']}/{values['target']}-{values['subarch']}-{values['version_stamp']}"
            snapshot = f"{CATALYST_SNAPSHOTS_PATH}/gentoo-{values['snapshot_treeish']}.sqfs"
            profile = values.get("profile", "")
            script = f"""#!/bin/bash
CHROOT="{chroot}"
REPO="$CHROOT/var/db/repos/gentoo"
echo "===== Diagnostics of failed build ====="
echo "--- chroot: $CHROOT"; ls "$CHROOT" | head -30
echo "--- make.profile:"; ls -l "$CHROOT/etc/portage/make.profile"
echo "--- make.conf:"; cat "$CHROOT/etc/portage/make.conf"
echo "--- /etc/portage:"; find "$CHROOT/etc/portage" -maxdepth 3 | head -60
echo "--- repos.conf:"; cat "$CHROOT"/etc/portage/repos.conf/* 2>&1 | head -20
echo "--- mounts in chroot:"; grep "$CHROOT" /proc/mounts
mkdir -p "$REPO"
echo "--- mounting snapshot {snapshot}:"; mount {"--bind" if rootless else "-o ro,loop"} "{snapshot}" "$REPO" && echo "mounted"
mount --bind /proc "$CHROOT/proc"
echo "--- repo profiles:"; ls "$REPO/profiles" | head -20
echo "--- profile {profile}:"; ls "$REPO/profiles/{profile}"
echo "--- portage in chroot:"
chroot "$CHROOT" /bin/bash -c 'for module in /usr/lib/python3*/site-packages/portage/__init__.py; do interpreter=$(echo $module | cut -d/ -f4); echo "using $interpreter"; $interpreter -c "import portage; print(portage.settings.profiles)"; $interpreter /tmp/build.py; echo "build.py exit code: $?"; done'
umount "$CHROOT/proc"
umount "$REPO"
echo "===== End of diagnostics ====="
"""
            script_path = os.path.join(work_directory, "diagnostics.sh")
            with open(script_path, "w", encoding="utf-8") as file:
                file.write(script)
            return script_path
        except Exception as e:
            self.log(f"Failed to prepare diagnostics: {e}")
            return None

# ------------------------------------------------------------------------------
# Helper functions.
# ------------------------------------------------------------------------------

# Catalyst options enabling caches.
_catalyst_cache_options = {
    StageArgumentDetails.pkgcache_path: "pkgcache",
    StageArgumentDetails.kerncache_path: "kerncache",
}

def _catalyst_build_script(spec_path: str, config_path: str, caches: set[str], wrapper_path: str | None = None) -> str:
    """Runs catalyst with /dev containing real device nodes. Catalyst bind mounts /dev into chroot without submounts,
    but device nodes in toolset /dev are bind mounts made by bwrap, so chroot would get empty files instead (portage
    fails with '/dev/null is not a character device'). Only standard nodes, loop devices and kvm are created.
    Catalyst uses configuration of toolset, with cache options enabled only for caches used by stage, and parallel
    builds using all processors when toolset doesn't configure them. Without jobs, catalyst sets empty MAKEOPTS,
    which disables default of portage (-j<processors>), so packages would be built one by one with single process.
    In rootless builds (wrapper_path set) /dev is prepared by session script, and catalyst runs through wrapper."""
    enabled = " ".join(sorted(caches))
    disabled = " ".join(sorted(set(_catalyst_cache_options.values()) - caches))
    if wrapper_path:
        run = f'exec python3 "{wrapper_path}" "${{CONFIG_ARGS[@]}}" -f "{spec_path}"'
    else:
        run = _CATALYST_DEV_SETUP + f'exec catalyst "${{CONFIG_ARGS[@]}}" -f "{spec_path}"'
    return f"""#!/bin/bash
# Fix of older catalyst: stage1 build.py concatenates portage Atom with str (TypeError with current portage). Fixed
# upstream, applied to toolset copy of this build session only (changes are not stored in toolset).
for build_py in /usr/share/catalyst/targets/stage1/build.py; do
    [ -f "$build_py" ] && grep -q 'sys.stdout.write(b + " ")' "$build_py" \
        && sed -i 's/sys.stdout.write(b + " ")/sys.stdout.write(str(b) + " ")/' "$build_py" \
        && echo "Applied stage1 build.py fix to catalyst"
done
# Catalyst configuration with cache options matching stage settings. Configuration of older catalyst (not TOML) is
# used unchanged.
CONFIG_ARGS=()
if python3 - "{config_path}" "{enabled}" "{disabled}" <<'PYTHON'
import json, os, sys, tomllib
path, enabled, disabled = sys.argv[1], sys.argv[2].split(), sys.argv[3].split()
with open("/etc/catalyst/catalyst.conf", "rb") as file:
    config = tomllib.load(file)
if any(isinstance(value, dict) for value in config.values()):
    sys.exit("Unsupported catalyst.conf structure")
options = [option for option in config.get("options", []) if option not in disabled]
config["options"] = options + [option for option in enabled if option not in options]
# emerge --jobs and --load-average, and MAKEOPTS (-j, -l). Load average limits parallel jobs to processors count.
processors = os.cpu_count() or 1
config.setdefault("jobs", processors)
config.setdefault("load-average", float(processors))
with open(path, "w") as file:
    for key, value in config.items():
        file.write(f"{{key}} = {{json.dumps(value)}}\\n")
print("Catalyst options: " + ", ".join(config["options"]))
print(f"Parallel jobs: {{config['jobs']}}, load average: {{config['load-average']}}")
PYTHON
then
    CONFIG_ARGS=(-c "{config_path}")
else
    echo "Using toolset catalyst.conf without changes"
fi
{run}
"""

_CATALYST_DEV_SETUP = """set -e
DEV=/tmp/catalystlab-dev
mkdir -p "$DEV"
mount -t tmpfs -o mode=0755,nosuid catalystlab-dev "$DEV"
mknod -m 666 "$DEV/null" c 1 3
mknod -m 666 "$DEV/zero" c 1 5
mknod -m 666 "$DEV/full" c 1 7
mknod -m 666 "$DEV/random" c 1 8
mknod -m 666 "$DEV/urandom" c 1 9
mknod -m 666 "$DEV/tty" c 5 0
# Devices bound into toolset (loop control, kvm), with the same numbers as on host.
for device in /dev/loop-control /dev/kvm; do
    [ -e "$device" ] || continue
    mknod -m 660 "$DEV/$(basename "$device")" c $((0x$(stat -c %t "$device"))) $((0x$(stat -c %T "$device")))
done
# Loop devices (block major 7). Catalyst gets free loop device from loop control, which can be created by kernel only
# now (eg. when others are used by mounted toolset), so its node wouldn't exist in host /dev bound earlier.
for number in $(seq 0 255); do
    mknod -m 660 "$DEV/loop$number" b 7 $number
done
mkdir -p "$DEV/pts" "$DEV/shm"
ln -s /proc/self/fd "$DEV/fd"
ln -s /proc/self/fd/0 "$DEV/stdin"
ln -s /proc/self/fd/1 "$DEV/stdout"
ln -s /proc/self/fd/2 "$DEV/stderr"
ln -s pts/ptmx "$DEV/ptmx"
mount --bind "$DEV" /dev
mount -t devpts -o newinstance,ptmxmode=0666,mode=0620 devpts /dev/pts
mount -t tmpfs -o mode=1777,nosuid,nodev shm /dev/shm
set +e
"""

def _strip_archive_extension(path: str) -> str:
    for extension in STAGE_ARCHIVE_EXTENSIONS:
        if path.endswith(extension):
            return path[:-len(extension)]
    return path

def _spec_values(spec: str) -> dict[str, str]:
    """Single line values of generated spec."""
    return {key.strip(): value.strip() for line in spec.splitlines() if ":" in line and not line.startswith("\t") for key, value in [line.split(":", 1)]}

@root_function
def prepare_loop_devices() -> list[str]:
    """Makes sure there is unused loop device and returns loop control and loop devices paths."""
    import subprocess, glob, re
    # Finding first unused loop device creates its node if all existing ones are in use.
    subprocess.run(["losetup", "--find"], check=True, stdout=subprocess.DEVNULL)
    devices = sorted(glob.glob("/dev/loop[0-9]*"), key=lambda path: int(re.sub(r"\D", "", path)))
    return (["/dev/loop-control"] if os.path.exists("/dev/loop-control") else []) + devices

def _move_stage_build_files(source_directory: str, name: str, destination_directory: str) -> list[str]:
    """Like move_stage_build_files, for files already owned by user (rootless builds)."""
    os.makedirs(destination_directory, exist_ok=True)
    moved = []
    if not os.path.isdir(source_directory):
        return moved
    for filename in sorted(os.listdir(source_directory)):
        if filename.startswith(name + "."):
            shutil.move(os.path.join(source_directory, filename), os.path.join(destination_directory, filename))
            moved.append(filename)
    return moved

@root_function
def move_stage_build_files(source_directory: str, name: str, destination_directory: str) -> list[str]:
    """Moves files produced by catalyst for given build name (archive, DIGESTS, CONTENTS...) to destination,
    and makes them owned by user."""
    import os, shutil
    uid = RootHelperServer.shared().uid
    os.makedirs(destination_directory, exist_ok=True)
    moved = []
    if not os.path.isdir(source_directory):
        return moved
    for filename in sorted(os.listdir(source_directory)):
        if filename.startswith(name + "."):
            target = os.path.join(destination_directory, filename)
            shutil.move(os.path.join(source_directory, filename), target)
            os.chown(target, uid, uid)
            moved.append(filename)
    return moved
