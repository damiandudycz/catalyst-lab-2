from __future__ import annotations
import os, threading, shutil
from datetime import datetime, timezone
from gi.repository import GLib
from .multistage_process import (
    MultiStageProcess, MultiStageProcessStage, MultiStageProcessState, MultiStageProcessStageState,
    MultiStageProcessEvent
)
from .root_function import root_function
from .repository import Repository
from .root_helper_server import ServerResponse, ServerResponseStatusCode
from .toolset import BindMount
from .toolset_env_builder import ToolsetEnvBuilder
from .project_stage_arguments import StageArgumentDetails
from .project_build import StageBuild, StageBuildStatus, StageBuildPlan, project_builds_directory, stage_builds_directory
from .project_build_spec import (
    StageSpecContext, generate_stage_spec, generate_portage_confdir, seed_name_prefix, select_seed_url
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
    only stages depending on it are skipped, so other branches are still built."""

    def __init__(self, project_directory, plan: StageBuildPlan):
        self.project_directory = project_directory
        self.plan = plan
        self.toolset = project_directory.get_toolset()
        self.timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") # @TIMESTAMP@ for all stages of this build.
        self.builds_directory = project_builds_directory(project_directory)
        self.seed_subpaths: dict = {} # Downloaded seeds of root stages, relative to builds directory.
        self.stage_builds: dict = {} # Builds made in this run, by stage id.
        self.failed_stage_ids: set = set()
        super().__init__(title=f"Building {project_directory.name}")

    def name(self) -> str:
        return self.project_directory.name

    def setup_stages(self):
        self.stages.append(ProjectBuildStepPrepareToolset(multistage_process=self))
        for stage in self.plan.build_order():
            if self.plan.parent(stage) is None:
                self.stages.append(ProjectBuildStepDownloadSeed(stage=stage, multistage_process=self))
            self.stages.append(ProjectBuildStepBuildStage(stage=stage, multistage_process=self))

    def container_path(self, host_path: str) -> str:
        """Path inside toolset of file in project builds directory."""
        return os.path.join(CATALYST_BUILDS_PATH, os.path.relpath(host_path, self.builds_directory))

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
                MultiStageProcess.event_bus.emit(
                    MultiStageProcessEvent.STARTED_PROCESSES_CHANGED,
                    self.__class__,
                    MultiStageProcess.get_started_processes_by_class(self.__class__)
                )
            self.complete_process(success=success)

    def complete_process(self, success: bool):
        pass

# ------------------------------------------------------------------------------
# Build steps.
# ------------------------------------------------------------------------------

class ProjectBuildStep(MultiStageProcessStage):
    def start(self):
        self.server_call = None
        super().start()
    def cancel(self):
        super().cancel()
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
            if toolset.spawned or not toolset.reserve():
                raise RuntimeError(f"Toolset {toolset.name} is in use. Close its environment and try again.")
            self.reserved = True
            os.makedirs(self.multistage_process.builds_directory, exist_ok=True)
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
    def required_bindings(self) -> list[BindMount]:
        process = self.multistage_process
        project = process.project_directory
        bindings = [
            # Catalyst writes builds here and reads seeds from it.
            BindMount(mount_path=CATALYST_BUILDS_PATH, host_path=process.builds_directory, store_changes=True, create_if_missing=True),
            BindMount(mount_path=CATALYST_SNAPSHOTS_PATH, host_path=Repository.Settings.value.snapshots_location, store_changes=True),
        ]
        # Directories referenced by spec values with host paths (@REPO_DIR@, root_overlay, repos) are available at the same paths.
        mirrored = [project.directory_path()]
        if releng_directory := project.get_releng_directory():
            mirrored.append(releng_directory.directory_path())
        mirrored += [overlay.directory_path() for overlay in Repository.OverlayDirectory.value]
        bindings += [BindMount(mount_path=path, host_path=path) for path in mirrored if os.path.isdir(path)]
        # Loop devices for mounting snapshot (store_changes gives read-write device access).
        bindings += [BindMount(mount_path=path, host_path=path, store_changes=True) for path in getattr(self, "loop_devices", [])]
        return bindings
    def cleanup(self) -> bool:
        if not super().cleanup():
            return False
        toolset = self.multistage_process.toolset
        if self.spawned:
            toolset.unspawn(rebuild_squashfs_if_needed=False)
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

class ProjectBuildStepBuildStage(ProjectBuildStep):
    def __init__(self, stage, multistage_process: ProjectBuild):
        super().__init__(name=f"Build {stage.name}", description=f"Builds {stage.target.replace('_', '-')} with catalyst", multistage_process=multistage_process)
        self.stage = stage
        self.build: StageBuild | None = None
    def start(self):
        super().start()
        process = self.multistage_process
        project = process.project_directory
        try:
            self.build = StageBuild(stage_id=self.stage.id, stage_name=self.stage.name, timestamp=process.timestamp)
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
            spec = generate_stage_spec(project, self.stage, StageSpecContext(
                timestamp=process.timestamp,
                source_subpath=source_subpath,
                portage_confdir=process.container_path(portage_path) if has_confdir else None,
            ))
            spec_path = os.path.join(work_directory, "stage.spec")
            with open(spec_path, "w", encoding="utf-8") as file:
                file.write(spec)
            self.log("Spec:")
            for line in spec.splitlines():
                self.log(f"  {line}")
            # Build:
            build_script_path = os.path.join(work_directory, "build.sh")
            with open(build_script_path, "w", encoding="utf-8") as file:
                file.write(_catalyst_build_script(spec_path=process.container_path(spec_path)))
            # Command is passed in single quotes, path in double quotes allows spaces in stage name.
            if not self.run_command_in_toolset(f'bash "{process.container_path(build_script_path)}"'):
                self._run_diagnostics(spec=spec, work_directory=work_directory)
                raise RuntimeError("Catalyst build failed")
            # Move results to stage build directory (catalyst writes them to builds/<rel_type>/):
            values = _spec_values(spec)
            output_name = f"{values['target']}-{values['subarch']}-{values['version_stamp']}"
            output_directory = os.path.join(process.builds_directory, values["rel_type"])
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

    def _run_diagnostics(self, spec: str, work_directory: str):
        """Collects details about chroot left by failed catalyst build. Catalyst hides errors of some scripts
        (eg. stage1 build.py), this runs the same checks with errors visible."""
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
echo "--- mounting snapshot {snapshot}:"; mount -o ro,loop "{snapshot}" "$REPO" && echo "mounted"
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
            self.run_command_in_toolset(f'bash "{process.container_path(script_path)}"')
        except Exception as e:
            self.log(f"Failed to run diagnostics: {e}")

# ------------------------------------------------------------------------------
# Helper functions.
# ------------------------------------------------------------------------------

def _catalyst_build_script(spec_path: str) -> str:
    """Runs catalyst with /dev containing real device nodes. Catalyst bind mounts /dev into chroot without submounts,
    but device nodes in toolset /dev are bind mounts made by bwrap, so chroot would get empty files instead (portage
    fails with '/dev/null is not a character device'). Only standard nodes, loop devices and kvm are created."""
    return f"""#!/bin/bash
set -e
DEV=/tmp/catalystlab-dev
mkdir -p "$DEV"
mount -t tmpfs -o mode=0755,nosuid catalystlab-dev "$DEV"
mknod -m 666 "$DEV/null" c 1 3
mknod -m 666 "$DEV/zero" c 1 5
mknod -m 666 "$DEV/full" c 1 7
mknod -m 666 "$DEV/random" c 1 8
mknod -m 666 "$DEV/urandom" c 1 9
mknod -m 666 "$DEV/tty" c 5 0
# Devices bound into toolset (loop devices for snapshot squashfs, kvm), with the same numbers as on host.
for device in /dev/loop-control /dev/loop[0-9]* /dev/kvm; do
    [ -e "$device" ] || continue
    type=c; [ -b "$device" ] && type=b
    mknod -m 660 "$DEV/$(basename "$device")" $type $((0x$(stat -c %t "$device"))) $((0x$(stat -c %T "$device")))
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
exec catalyst -f "{spec_path}"
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
