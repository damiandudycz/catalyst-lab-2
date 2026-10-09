from __future__ import annotations
import os
from dataclasses import dataclass
from .multistage_process import MultiStageProcess, MultiStageProcessStage, MultiStageProcessStageState
from .ssh_connection import SSHConnection, quote
from .deploy_target import TargetMachine, PartitionPlan, grub_target

# ------------------------------------------------------------------------------
# Deploying stage build (stage3/stage4 tarball) to machine booted from Gentoo LiveCD, through SSH: disk is
# partitioned and formatted, stage is extracted to it and system is configured.
# ------------------------------------------------------------------------------

MOUNT_POINT = "/mnt/gentoo"

@dataclass
class DeploySystemSettings:
    hostname: str | None = None
    root_password: str | None = None
    enable_ssh: bool = False
    install_bootloader: bool = True
    reboot: bool = False

def tar_extract_command(artifact: str) -> str:
    """Command extracting stage tarball read from stdin, keeping owners and extended attributes."""
    compression = next((option for extension, option in (
        (".tar.xz", "-J"), (".tar.zst", "--zstd"), (".tar.zstd", "--zstd"), (".tar.bz2", "-j"), (".tar.gz", "-z"),
        (".tar.lz4", "--use-compress-program=lz4"), (".tar.lzma", "--lzma"), (".tar", ""),
    ) if artifact.endswith(extension)), None)
    if compression is None:
        raise RuntimeError(f"Unsupported archive: {artifact}")
    return f"tar -x -p {compression} --xattrs-include='*.*' --numeric-owner -C {MOUNT_POINT} -f -"

class DeployInstallation(MultiStageProcess):
    """Installs stage build on disk of machine connected through SSH."""

    def __init__(self, build, project_name: str, connection: SSHConnection, machine: TargetMachine, plan: PartitionPlan,
                 settings: DeploySystemSettings):
        self.build = build
        self.project_name = project_name
        self.connection = connection
        self.machine = machine
        self.plan = plan
        self.settings = settings
        self.mounted = False
        super().__init__(title="Deploy")

    def setup_stages(self):
        self.stages.append(DeployStepCheck(multistage_process=self))
        self.stages.append(DeployStepPartition(multistage_process=self))
        self.stages.append(DeployStepFormat(multistage_process=self))
        self.stages.append(DeployStepMount(multistage_process=self))
        self.stages.append(DeployStepExtract(multistage_process=self))
        self.stages.append(DeployStepConfigure(multistage_process=self))
        if self.settings.install_bootloader:
            self.stages.append(DeployStepBootloader(multistage_process=self))
        self.stages.append(DeployStepFinish(multistage_process=self))

    def name(self) -> str:
        return f"{self.build.stage_name} on {self.connection.host}"

    def complete_process(self, success: bool):
        # Partitions stay mounted after failure, so their files can be checked. Connection is closed.
        try:
            self.connection.close()
        except Exception as e:
            print(f"Failed to close SSH connection: {e}")

class DeployStep(MultiStageProcessStage):
    """Step running scripts on machine, cancelled by stopping them."""
    def start(self):
        self.processes = []
        super().start()
        try:
            self.run()
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Error during '{self.name}': {e}")
            self.complete(MultiStageProcessStageState.FAILED)
    def run(self):
        raise NotImplementedError
    def remote(self, script: str, error: str):
        if not self.multistage_process.connection.run(script, self.log, self.processes):
            raise RuntimeError(error)
    def cancel(self):
        super().cancel()
        for process in getattr(self, "processes", []):
            if process.poll() is None:
                process.terminate()

class DeployStepCheck(DeployStep):
    def __init__(self, multistage_process):
        super().__init__(name="Check machine", description="Checks connection and tools needed for installation", multistage_process=multistage_process)
    def run(self):
        process = self.multistage_process
        if not process.connection.is_alive():
            raise RuntimeError(f"Connection to {process.connection.host} was lost, connect again")
        tools = " ".join(quote(tool) for tool in process.plan.required_tools())
        self.remote(f"""
missing=""
for tool in {tools}; do command -v "$tool" > /dev/null || missing="$missing $tool"; done
[ -z "$missing" ] || {{ echo "Missing tools:$missing"; exit 1; }}
echo "Machine: $(uname -n), $(uname -m), kernel $(uname -r)"
lsblk {quote(process.plan.disk.path)}
""", "Machine doesn't have tools needed for installation")
        if not os.path.isfile(process.build.artifact_path or ""):
            raise RuntimeError("Build archive doesn't exist anymore")

class DeployStepPartition(DeployStep):
    def __init__(self, multistage_process):
        super().__init__(name="Partition disk", description="Creates new partition table on selected disk", multistage_process=multistage_process)
    def run(self):
        self.remote(self.multistage_process.plan.partition_script(), "Failed to partition disk")

class DeployStepFormat(DeployStep):
    def __init__(self, multistage_process):
        super().__init__(name="Format partitions", description="Creates filesystems", multistage_process=multistage_process)
    def run(self):
        self.remote(self.multistage_process.plan.format_script(), "Failed to format partitions")

class DeployStepMount(DeployStep):
    def __init__(self, multistage_process):
        super().__init__(name="Mount partitions", description=f"Mounts new filesystems at {MOUNT_POINT}", multistage_process=multistage_process)
    def run(self):
        self.remote(self.multistage_process.plan.mount_script(), "Failed to mount partitions")
        self.multistage_process.mounted = True

class DeployStepExtract(DeployStep):
    def __init__(self, multistage_process):
        super().__init__(name="Install stage", description="Sends stage archive and extracts it on new root filesystem", multistage_process=multistage_process)
    def run(self):
        process = self.multistage_process
        path = process.build.artifact_path
        self.log(f"Extracting {os.path.basename(path)} ({os.path.getsize(path) // (1024 * 1024)} MiB) to {MOUNT_POINT}")
        command = tar_extract_command(path)
        if not process.connection.stream_file(path, command, self.log, self._update_progress, self.processes):
            raise RuntimeError("Failed to extract stage")
        self.remote(f"sync; df -h {MOUNT_POINT}", "Failed to check installed files")

class DeployStepConfigure(DeployStep):
    def __init__(self, multistage_process):
        super().__init__(name="Configure system", description="Sets filesystems table, hostname, root password and services", multistage_process=multistage_process)
    def run(self):
        process = self.multistage_process
        settings = process.settings
        self.remote(process.plan.fstab_script(), "Failed to write fstab")
        script = ["set -e", f"ROOT={MOUNT_POINT}", 'cp -L /etc/resolv.conf "$ROOT/etc/resolv.conf"']
        if settings.hostname:
            hostname = quote(settings.hostname)
            script += [
                f'echo {hostname} > "$ROOT/etc/hostname"',
                # OpenRC reads hostname from conf.d.
                f'[ -f "$ROOT/etc/conf.d/hostname" ] && sed -i "s/^hostname=.*/hostname=\\"$(cat "$ROOT/etc/hostname")\\"/" "$ROOT/etc/conf.d/hostname"',
                f'echo "Hostname set to {settings.hostname}"',
            ]
        if settings.root_password:
            # Password is sent in script (stdin of ssh), not as command argument.
            script += [f"echo {quote('root:' + settings.root_password)} | chroot \"$ROOT\" chpasswd", 'echo "Root password set"']
        if settings.enable_ssh:
            script += [
                'if [ -x "$ROOT/sbin/openrc-run" ] || [ -x "$ROOT/usr/bin/openrc-run" ] || [ -d "$ROOT/etc/runlevels" ]; then',
                '    if [ -e "$ROOT/etc/init.d/sshd" ]; then ln -sf /etc/init.d/sshd "$ROOT/etc/runlevels/default/sshd"; echo "SSH server enabled (OpenRC)"; else echo "Warning: SSH server is not installed in stage"; fi',
                'elif [ -e "$ROOT/usr/lib/systemd/system/sshd.service" ]; then',
                '    mkdir -p "$ROOT/etc/systemd/system/multi-user.target.wants"',
                '    ln -sf /usr/lib/systemd/system/sshd.service "$ROOT/etc/systemd/system/multi-user.target.wants/sshd.service"',
                '    echo "SSH server enabled (systemd)"',
                'else',
                '    echo "Warning: SSH server is not installed in stage"',
                'fi',
            ]
        # Stage3 doesn't contain kernel, system can't boot without it.
        script += [
            'if ls "$ROOT"/boot/vmlinu* "$ROOT"/boot/kernel* "$ROOT"/boot/Image* > /dev/null 2>&1; then',
            '    echo "Kernel: $(cd "$ROOT/boot" && ls vmlinu* kernel* Image* 2>/dev/null | tr \'\\n\' \' \')"',
            'else',
            '    echo "Warning: stage doesn\'t contain kernel in /boot, install one before booting (eg. sys-kernel/gentoo-kernel-bin)"',
            'fi',
        ]
        self.remote("\n".join(script) + "\n", "Failed to configure system")

class DeployStepBootloader(DeployStep):
    def __init__(self, multistage_process):
        super().__init__(name="Install bootloader", description="Installs GRUB, when stage contains it", multistage_process=multistage_process)
    def run(self):
        process = self.multistage_process
        target = grub_target(process.machine.architecture, process.machine.uefi)
        if target is None:
            self.log(f"GRUB is not supported on {process.machine.architecture} with {'UEFI' if process.machine.uefi else 'BIOS'} firmware, bootloader was not installed")
            return
        if process.machine.uefi:
            install = f"grub-install --target={target} --efi-directory=/efi --bootloader-id=Gentoo"
        else:
            install = f"grub-install --target={target} {quote(process.plan.disk.path)}"
        self.remote(f"""set -e
ROOT={MOUNT_POINT}
if [ ! -x "$ROOT/usr/sbin/grub-install" ] && [ ! -x "$ROOT/usr/bin/grub-install" ]; then
    echo "GRUB is not installed in stage (sys-boot/grub), bootloader was not installed"
    exit 0
fi
mount --types proc /proc "$ROOT/proc"
mount --rbind /sys "$ROOT/sys" && mount --make-rslave "$ROOT/sys"
mount --rbind /dev "$ROOT/dev" && mount --make-rslave "$ROOT/dev"
trap 'umount -l "$ROOT/dev" "$ROOT/sys" "$ROOT/proc" 2>/dev/null' EXIT
chroot "$ROOT" {install}
chroot "$ROOT" grub-mkconfig -o /boot/grub/grub.cfg
""", "Failed to install bootloader")

class DeployStepFinish(DeployStep):
    def __init__(self, multistage_process):
        super().__init__(name="Finish", description="Unmounts filesystems", multistage_process=multistage_process)
    def run(self):
        process = self.multistage_process
        self.remote(f"sync; umount -R {MOUNT_POINT}; echo 'Installation finished'", "Failed to unmount filesystems")
        process.mounted = False
        if process.settings.reboot:
            self.log("Rebooting machine")
            process.connection.run("(sleep 2; reboot) > /dev/null 2>&1 &", self.log, self.processes)

# ------------------------------------------------------------------------------
# Builds that can be deployed: completed stage3 and stage4 builds with their archives. Live CDs and other images are
# not installed this way.

DEPLOYABLE_TARGETS = ("stage3", "stage4")

def is_deployable(project_directory, build) -> bool:
    from .project_build import StageBuildStatus
    if build.status != StageBuildStatus.COMPLETED or not build.artifact or ".tar" not in build.artifact:
        return False
    if not os.path.isfile(build.artifact_path):
        return False
    stage = next((item for item in project_directory.stages if item.id == build.stage_id), None)
    # Stage could be removed from project, its archive name tells its target then.
    target = (stage.target if stage else build.artifact.split("-")[0]).replace("-", "_")
    return target in DEPLOYABLE_TARGETS

def deployable_builds() -> list[tuple]:
    """(project, builds) for projects having deployable builds, newest builds first."""
    from .repository import Repository
    from .project_build import load_project_builds
    result = []
    for project in Repository.ProjectDirectory.value:
        try:
            builds = [build for build in load_project_builds(project) if is_deployable(project, build)]
        except Exception as e:
            print(f"Failed to load builds of {project.name}: {e}")
            continue
        if builds:
            result.append((project, builds))
    return result
