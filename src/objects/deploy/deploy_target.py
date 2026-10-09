from __future__ import annotations
import json, re
from dataclasses import dataclass, field
from enum import Enum
from .ssh_connection import SSHConnection, quote

# ------------------------------------------------------------------------------
# Machine where build is deployed, its disks and partitioning of selected disk.

MIB = 1024 * 1024
GIB = 1024 * MIB

@dataclass
class TargetDisk:
    path: str
    size: int # Bytes.
    model: str | None = None
    transport: str | None = None
    removable: bool = False
    in_use: bool = False # Has mounted partitions (eg. LiveCD media).

    @property
    def title(self) -> str:
        return f"{self.path} · {format_size(self.size)}"

    @property
    def subtitle(self) -> str:
        details = [value for value in (self.model, (self.transport or "").upper() or None, "removable" if self.removable else None) if value]
        if self.in_use:
            details.append("in use, has mounted partitions")
        return ", ".join(details) or "Disk"

@dataclass
class TargetMachine:
    """Details of machine read through SSH connection."""
    architecture: str # uname -m
    uefi: bool
    memory: int # Bytes.
    hostname: str
    disks: list[TargetDisk] = field(default_factory=list)

    _SCRIPT = r'''
echo "architecture=$(uname -m)"
[ -d /sys/firmware/efi ] && echo "firmware=uefi" || echo "firmware=bios"
echo "memory=$(awk '/MemTotal/ {print $2 * 1024}' /proc/meminfo)"
echo "hostname=$(hostname)"
echo "disks=$(lsblk -J -b -o NAME,PATH,SIZE,MODEL,TYPE,RO,RM,TRAN,MOUNTPOINTS | tr -d '\n')"
'''

    @classmethod
    def load(cls, connection: SSHConnection) -> TargetMachine:
        values = dict(line.split("=", 1) for line in connection.output(cls._SCRIPT).splitlines() if "=" in line)
        machine = cls(
            architecture=values.get("architecture", "unknown"),
            uefi=values.get("firmware") == "uefi",
            memory=int(values.get("memory") or 0),
            hostname=values.get("hostname", ""),
        )
        for device in json.loads(values.get("disks") or "{}").get("blockdevices", []):
            if device.get("type") != "disk" or _flag(device.get("ro")) or int(device.get("size") or 0) == 0:
                continue
            machine.disks.append(TargetDisk(
                path=device.get("path") or f"/dev/{device['name']}",
                size=int(device["size"]),
                model=(device.get("model") or "").strip() or None,
                transport=device.get("tran"),
                removable=_flag(device.get("rm")),
                in_use=_has_mountpoints(device),
            ))
        return machine

    @property
    def description(self) -> str:
        return f"{self.architecture}, {'UEFI' if self.uefi else 'BIOS'} firmware, {format_size(self.memory)} memory"

def _flag(value) -> bool:
    return value in (True, 1, "1", "true")

def _has_mountpoints(device: dict) -> bool:
    if any(device.get("mountpoints") or []):
        return True
    return any(_has_mountpoints(child) for child in device.get("children") or [])

def format_size(size: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024

# Architectures of builds, as uname -m of machines that can run them.
_MACHINE_ARCHITECTURES = {
    "amd64": {"x86_64"},
    "x86": {"i686", "i586", "i486", "x86_64"},
    "arm64": {"aarch64"},
    "arm": {"armv7l", "armv6l", "armv5tel", "aarch64"},
    "ppc64": {"ppc64"},
    "ppc64le": {"ppc64le"},
    "riscv": {"riscv64"},
}

def architecture_supported(build_architecture: str | None, machine_architecture: str) -> bool | None:
    """Whether machine can run build of given architecture, None when unknown."""
    supported = _MACHINE_ARCHITECTURES.get(build_architecture or "")
    return None if supported is None else machine_architecture in supported

# ------------------------------------------------------------------------------
# Partitioning:

class TargetFilesystem(Enum):
    EXT4 = "ext4"
    XFS = "xfs"
    BTRFS = "btrfs"

    @property
    def format_command(self) -> str:
        return {
            TargetFilesystem.EXT4: "mkfs.ext4 -F -L root",
            TargetFilesystem.XFS: "mkfs.xfs -f -L root",
            TargetFilesystem.BTRFS: "mkfs.btrfs -f -L root",
        }[self]

@dataclass
class TargetPartition:
    name: str
    size: int | None # Bytes, None for the rest of disk.
    type: str # GPT partition type GUID.
    mount_point: str | None # None for swap and BIOS boot.
    filesystem: str | None # fstab type.
    format_command: str | None

_GPT_EFI = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
_GPT_BIOS_BOOT = "21686148-6449-6E6F-744E-656564454649"
_GPT_SWAP = "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F"
_GPT_LINUX = "0FC63DAF-8483-4772-8E79-3D69D7984743"

@dataclass
class PartitionPlan:
    """Whole disk is used: EFI system partition (UEFI) or BIOS boot partition, optional swap, root filesystem."""
    disk: TargetDisk
    uefi: bool
    efi_size: int = 1 * GIB
    swap_size: int = 0
    filesystem: TargetFilesystem = TargetFilesystem.EXT4

    @property
    def partitions(self) -> list[TargetPartition]:
        partitions = []
        if self.uefi:
            partitions.append(TargetPartition("EFI system", self.efi_size, _GPT_EFI, "/efi", "vfat", "mkfs.vfat -F 32 -n EFI"))
        else:
            partitions.append(TargetPartition("BIOS boot", 1 * MIB, _GPT_BIOS_BOOT, None, None, None))
        if self.swap_size > 0:
            partitions.append(TargetPartition("Swap", self.swap_size, _GPT_SWAP, None, "swap", "mkswap -L swap"))
        partitions.append(TargetPartition("Root", None, _GPT_LINUX, "/", self.filesystem.value, self.filesystem.format_command))
        return partitions

    @property
    def root_size(self) -> int:
        # GPT and alignment take few MiB.
        return self.disk.size - sum(partition.size for partition in self.partitions if partition.size) - 4 * MIB

    def partition_device(self, index: int) -> str:
        """Device of partition (1-based), eg. /dev/sda1 or /dev/nvme0n1p1."""
        separator = "p" if re.search(r"\d$", self.disk.path) else ""
        return f"{self.disk.path}{separator}{index}"

    def sfdisk_script(self) -> str:
        lines = ["label: gpt"]
        for partition in self.partitions:
            size = f"size={partition.size // MIB}MiB, " if partition.size else ""
            lines.append(f"{size}type={partition.type}, name=\"{partition.name}\"")
        return "\n".join(lines) + "\n"

    # Scripts run on machine:

    def partition_script(self) -> str:
        disk = quote(self.disk.path)
        return f"""set -e
swapoff -a 2>/dev/null || true
umount -R /mnt/gentoo 2>/dev/null || true
echo "Removing old partitions and signatures from {self.disk.path}"
wipefs -a {disk}
sfdisk --wipe always --wipe-partitions always {disk} <<'SFDISK'
{self.sfdisk_script()}SFDISK
partprobe {disk} 2>/dev/null || blockdev --rereadpt {disk} 2>/dev/null || true
udevadm settle 2>/dev/null || sleep 2
sfdisk -l {disk}
"""

    def format_script(self) -> str:
        lines = ["set -e"]
        for index, partition in enumerate(self.partitions, start=1):
            if partition.format_command:
                device = quote(self.partition_device(index))
                lines.append(f'echo "Formatting {partition.name} partition ({partition.filesystem})"')
                lines.append(f"{partition.format_command} {device}")
        return "\n".join(lines) + "\n"

    def mount_script(self) -> str:
        lines = ["set -e", "mkdir -p /mnt/gentoo"]
        mounted = sorted(
            ((index, partition) for index, partition in enumerate(self.partitions, start=1) if partition.mount_point),
            key=lambda item: len(item[1].mount_point)
        )
        for index, partition in mounted:
            target = "/mnt/gentoo" + (partition.mount_point if partition.mount_point != "/" else "")
            lines.append(f"mkdir -p {quote(target)}")
            lines.append(f"mount {quote(self.partition_device(index))} {quote(target)}")
        lines.append("df -h /mnt/gentoo")
        return "\n".join(lines) + "\n"

    def fstab_script(self) -> str:
        """Appends partitions to /etc/fstab of installed system, using UUIDs."""
        lines = ["set -e", "FSTAB=/mnt/gentoo/etc/fstab", 'echo "" >> "$FSTAB"', 'echo "# Added by Catalyst Lab" >> "$FSTAB"']
        for index, partition in enumerate(self.partitions, start=1):
            if not partition.filesystem:
                continue
            device = quote(self.partition_device(index))
            if partition.filesystem == "swap":
                entry = 'none swap sw 0 0'
            elif partition.mount_point == "/":
                entry = f"/ {partition.filesystem} defaults,noatime 0 1"
            else:
                entry = f"{partition.mount_point} {partition.filesystem} defaults,noatime,umask=0077 0 2"
            lines.append(f'echo "UUID=$(blkid -s UUID -o value {device}) {entry}" >> "$FSTAB"')
        lines.append('cat "$FSTAB"')
        return "\n".join(lines) + "\n"

    def required_tools(self) -> list[str]:
        tools = ["wipefs", "sfdisk", "blkid", "tar"]
        tools += [partition.format_command.split()[0] for partition in self.partitions if partition.format_command]
        return tools

def grub_target(architecture: str, uefi: bool) -> str | None:
    """GRUB platform for machine architecture."""
    if not uefi:
        return "i386-pc" if architecture in ("x86_64", "i686", "i586", "i486") else None
    return {"x86_64": "x86_64-efi", "i686": "i386-efi", "aarch64": "arm64-efi", "riscv64": "riscv64-efi",
            "armv7l": "arm-efi"}.get(architecture)
