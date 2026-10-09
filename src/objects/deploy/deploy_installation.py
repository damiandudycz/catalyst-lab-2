from __future__ import annotations
import os
from dataclasses import dataclass, field
from .multistage_process import MultiStageProcess, MultiStageProcessStage, MultiStageProcessStageState
from .ssh_connection import SSHConnection, quote
from .deploy_target import TargetMachine, PartitionPlan, PartitionType
from .deploy_boot import Bootloader, Kernel, FIRMWARE_PACKAGE, StageContents, packages_script, bootloader_script, grub_platform
from .deploy_system import (
    NetworkService, NetworkSettings, LocalizationSettings, network_script, localization_script, authorized_keys_script
)

# ------------------------------------------------------------------------------
# Deploying stage build (stage3/stage4 tarball) to machine booted from Gentoo LiveCD, through SSH: disk is
# partitioned and formatted, stage is extracted to it and system is configured.
# ------------------------------------------------------------------------------

MOUNT_POINT = "/mnt/gentoo"

@dataclass
class DeployUser:
    name: str
    password: str
    full_name: str = ""
    groups: list[str] = field(default_factory=list)

    @property
    def gecos(self) -> str:
        """Comment field of account (fields are separated with commas, entries with colons)."""
        return self.full_name.replace(",", " ").replace(":", " ").strip()

# Groups suggested for new users.
DEFAULT_USER_GROUPS = ["wheel", "audio", "video", "usb", "users"]

@dataclass
class DeploySystemSettings:
    hostname: str | None = None
    root_password: str | None = None
    users: list[DeployUser] = field(default_factory=list)
    enable_ssh: bool = False
    ssh_keys: list[str] = field(default_factory=list) # Authorized for root and users.
    network: NetworkSettings = field(default_factory=NetworkSettings)
    localization: LocalizationSettings = field(default_factory=LocalizationSettings)
    bootloader: Bootloader = Bootloader.NONE
    kernel: Kernel = Kernel.STAGE
    install_firmware: bool = False
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
                 settings: DeploySystemSettings, contents: StageContents):
        self.build = build
        self.project_name = project_name
        self.project_id = None # Set by wizard, for status of project in Deploy section.
        self.connection = connection
        self.machine = machine
        self.plan = plan
        self.settings = settings
        self.contents = contents
        self.mounted = False
        self._log_file = None
        self.log_path = self._new_log_path()
        super().__init__(title="Deploy")

    def setup_stages(self):
        self.stages.append(DeployStepCheck(multistage_process=self))
        self.stages.append(DeployStepPartition(multistage_process=self))
        self.stages.append(DeployStepFormat(multistage_process=self))
        self.stages.append(DeployStepMount(multistage_process=self))
        self.stages.append(DeployStepExtract(multistage_process=self))
        self.stages.append(DeployStepConfigure(multistage_process=self))
        if self.packages:
            self.stages.append(DeployStepPackages(multistage_process=self))
        if self.settings.network.service != NetworkService.NONE:
            self.stages.append(DeployStepNetwork(multistage_process=self))
        if self.settings.bootloader != Bootloader.NONE:
            self.stages.append(DeployStepBootloader(multistage_process=self))
        self.stages.append(DeployStepFinish(multistage_process=self))

    def name(self) -> str:
        return f"{self.build.stage_name} on {self.connection.host}"

    @property
    def packages(self) -> list[str]:
        """Packages installed from Gentoo repository: bootloader missing in stage, and kernel."""
        packages = []
        bootloader = self.settings.bootloader
        if bootloader.needs_package and bootloader not in self.contents.bootloaders:
            packages.append(bootloader.package(self.contents.init))
        service = self.settings.network.service
        if service.package and service not in self.contents.network_services:
            packages.append(service.package)
        if self.settings.install_firmware:
            packages.append(FIRMWARE_PACKAGE) # Before kernel, so its initramfs includes firmware.
        if self.settings.kernel.package:
            packages.append(self.settings.kernel.package)
        return packages

    # Log of deployment is saved next to build, like build.log.

    def _new_log_path(self) -> str | None:
        if not self.build.path:
            return None
        from datetime import datetime
        host = "".join(character if character.isalnum() or character in ".-" else "_" for character in self.connection.host)
        return os.path.join(self.build.path, f"deploy-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{host}.log")

    def write_log(self, line: str):
        if not self.log_path:
            return
        try:
            if self._log_file is None:
                self._log_file = open(self.log_path, "a", encoding="utf-8", buffering=1)
            self._log_file.write(line + "\n")
        except OSError as e:
            print(f"Failed to write deploy log: {e}")

    def complete_process(self, success: bool):
        self.write_log(f"=== Deployment {'completed' if success else 'failed'}")
        if self._log_file:
            self._log_file.close()
            self._log_file = None
        # Partitions stay mounted after failure, so their files can be checked. Connection is closed.
        try:
            self.connection.close()
        except Exception as e:
            print(f"Failed to close SSH connection: {e}")

class DeployStep(MultiStageProcessStage):
    """Step running scripts on machine, cancelled by stopping them."""
    def log(self, line: str):
        super().log(line)
        self.multistage_process.write_log(line)
    def start(self):
        self.processes = []
        super().start()
        self.multistage_process.write_log(f"=== {self.name}")
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
        super().__init__(name="Configure system", description="Sets filesystems table, hostname, users and services", multistage_process=multistage_process)
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
        script.append(localization_script(settings.localization))
        for user in settings.users:
            # Groups missing in stage are skipped.
            groups = " ".join(quote(group) for group in user.groups)
            script += [
                f'GROUPS_LIST=""; for group in {groups}; do if grep -q "^$group:" "$ROOT/etc/group"; then GROUPS_LIST="$GROUPS_LIST${{GROUPS_LIST:+,}}$group"; else echo "Group $group doesn\'t exist, skipped"; fi; done',
                f'chroot "$ROOT" useradd -m -s /bin/bash -c {quote(user.gecos)} ${{GROUPS_LIST:+-G "$GROUPS_LIST"}} {quote(user.name)}',
                f"echo {quote(user.name + ':' + user.password)} | chroot \"$ROOT\" chpasswd",
                f'echo "User {user.name} created${{GROUPS_LIST:+, groups $GROUPS_LIST}}"',
            ]
        script.append(authorized_keys_script(settings.ssh_keys, [user.name for user in settings.users]))
        # Stage3 doesn't contain kernel, system can't boot without it.
        if settings.kernel in (Kernel.STAGE, Kernel.NONE):
            script += [
                'if ls "$ROOT"/boot/vmlinu* "$ROOT"/boot/kernel* "$ROOT"/boot/Image* > /dev/null 2>&1; then',
                '    echo "Kernel: $(cd "$ROOT/boot" && ls vmlinu* kernel* Image* 2>/dev/null | tr \'\\n\' \' \')"',
                'else',
                '    echo "Warning: stage doesn\'t contain kernel in /boot, install one before booting (eg. sys-kernel/gentoo-kernel-bin)"',
                'fi',
            ]
        self.remote("\n".join(script) + "\n", "Failed to configure system")

class DeployStepPackages(DeployStep):
    def __init__(self, multistage_process):
        super().__init__(name="Install packages", description="Installs kernel, firmware and bootloader from Gentoo repository", multistage_process=multistage_process)
    def run(self):
        process = self.multistage_process
        platform = grub_platform(process.machine.architecture, process.machine.uefi) if "sys-boot/grub" in process.packages else None
        script = f"ROOT={MOUNT_POINT}\n" + packages_script(process.packages, platform, process.contents.init)
        self.remote(script, "Failed to install packages, machine needs internet connection")

class DeployStepNetwork(DeployStep):
    def __init__(self, multistage_process):
        super().__init__(name="Configure network", description=f"Enables {multistage_process.settings.network.service.display_name}", multistage_process=multistage_process)
    def run(self):
        script = network_script(self.multistage_process.settings.network)
        self.remote(f"set -e\nROOT={MOUNT_POINT}\n{script}\n", "Failed to configure network")

class DeployStepBootloader(DeployStep):
    def __init__(self, multistage_process):
        bootloader = multistage_process.settings.bootloader
        super().__init__(name="Install bootloader", description=f"Installs and configures {bootloader.display_name}", multistage_process=multistage_process)
    def run(self):
        process = self.multistage_process
        plan = process.plan
        efi_index = plan.index_of(lambda spec: spec.type == PartitionType.EFI)
        root_index = plan.index_of(lambda spec: spec.type == PartitionType.LINUX and spec.mount_point == "/")
        script = bootloader_script(
            process.settings.bootloader, process.machine.architecture, process.machine.uefi, plan.disk.path,
            root_device=plan.partition_device(root_index),
            efi_device=plan.partition_device(efi_index) if efi_index else None,
            esp=plan.efi_mount_point,
            separate_boot=plan.index_of(lambda spec: spec.type == PartitionType.LINUX and spec.mount_point == "/boot") is not None,
        )
        if script is None:
            raise RuntimeError(f"{process.settings.bootloader.display_name} is not supported on this machine")
        self.remote(f"ROOT={MOUNT_POINT}\n" + script, "Failed to install bootloader")

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
