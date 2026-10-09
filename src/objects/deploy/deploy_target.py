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
    # Network used by SSH connection, suggested for static configuration.
    interface: str = ""
    address: str = ""
    gateway: str = ""
    dns: str = ""

    _SCRIPT = r'''
echo "architecture=$(uname -m)"
[ -d /sys/firmware/efi ] && echo "firmware=uefi" || echo "firmware=bios"
echo "memory_kib=$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
echo "hostname=$(hostname)"
echo "disks=$(lsblk -J -b -o NAME,PATH,SIZE,MODEL,TYPE,RO,RM,TRAN,MOUNTPOINTS | tr -d '\n')"
INTERFACE=$(ip -o route get "${SSH_CLIENT%% *}" 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p')
echo "interface=$INTERFACE"
[ -n "$INTERFACE" ] && echo "address=$(ip -o -4 addr show dev "$INTERFACE" | awk '{print $4; exit}')"
echo "gateway=$(ip -4 route show default | awk '{print $3; exit}')"
echo "dns=$(awk '/^nameserver/ {print $2}' /etc/resolv.conf | tr '\n' ' ')"
'''

    @classmethod
    def load(cls, connection: SSHConnection) -> TargetMachine:
        values = dict(line.split("=", 1) for line in connection.output(cls._SCRIPT).splitlines() if "=" in line)
        machine = cls(
            architecture=values.get("architecture", "unknown"),
            uefi=values.get("firmware") == "uefi",
            memory=int(values.get("memory_kib") or 0) * 1024, # Bytes overflow formatting of some awk versions.
            hostname=values.get("hostname", ""),
            interface=values.get("interface", ""),
            address=values.get("address", ""),
            gateway=values.get("gateway", ""),
            dns=values.get("dns", "").strip(),
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
# Partitioning: new partition table (GPT or MBR) with partitions defined by user, in order. Last partition can
# use remaining space of disk. MBR tables have only primary partitions (up to 4).

class PartitionTable(Enum):
    GPT = "gpt"
    MBR = "dos"

    @property
    def display_name(self) -> str:
        return {PartitionTable.GPT: "GPT", PartitionTable.MBR: "MBR (DOS)"}[self]

MBR_MAX_PARTITIONS = 4
MBR_MAX_SIZE = 2 * 1024 * GIB

class PartitionType(Enum):
    EFI = "efi"
    BIOS_BOOT = "bios-boot"
    LINUX = "linux"
    SWAP = "swap"

    @property
    def display_name(self) -> str:
        return {PartitionType.EFI: "EFI system", PartitionType.BIOS_BOOT: "BIOS boot", PartitionType.LINUX: "Linux filesystem",
                PartitionType.SWAP: "Swap"}[self]

    @property
    def gpt_type(self) -> str:
        return {
            PartitionType.EFI: "C12A7328-F81F-11D2-BA4B-00A0C93EC93B",
            PartitionType.BIOS_BOOT: "21686148-6449-6E6F-744E-656564454649",
            PartitionType.LINUX: "0FC63DAF-8483-4772-8E79-3D69D7984743",
            PartitionType.SWAP: "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F",
        }[self]

    @property
    def mbr_type(self) -> str | None:
        return {PartitionType.EFI: "ef", PartitionType.LINUX: "83", PartitionType.SWAP: "82"}.get(self)

class TargetFilesystem(Enum):
    EXT4 = "ext4"
    XFS = "xfs"
    BTRFS = "btrfs"
    VFAT = "vfat"

    def format_command(self, label: str) -> str:
        label = quote(label[:11].upper() if self == TargetFilesystem.VFAT else label[:16])
        return {
            TargetFilesystem.EXT4: f"mkfs.ext4 -F -L {label}",
            TargetFilesystem.XFS: f"mkfs.xfs -f -L {label}",
            TargetFilesystem.BTRFS: f"mkfs.btrfs -f -L {label}",
            TargetFilesystem.VFAT: f"mkfs.vfat -F 32 -n {label}",
        }[self]

@dataclass
class PartitionSpec:
    """Partition defined by user."""
    type: PartitionType
    size: int | None # Bytes, None uses remaining space of disk.
    filesystem: TargetFilesystem | None = None # Linux filesystem partitions.
    mount_point: str | None = None # Linux filesystem and EFI partitions.

    @property
    def effective_filesystem(self) -> TargetFilesystem | None:
        match self.type:
            case PartitionType.EFI: return TargetFilesystem.VFAT
            case PartitionType.LINUX: return self.filesystem or TargetFilesystem.EXT4
        return None

    @property
    def label(self) -> str:
        """Name of partition and filesystem label."""
        match self.type:
            case PartitionType.EFI: return "EFI"
            case PartitionType.BIOS_BOOT: return "BIOS boot"
            case PartitionType.SWAP: return "swap"
        return "root" if self.mount_point == "/" else (self.mount_point or "data").strip("/").replace("/", "-")

@dataclass
class TargetPartition:
    name: str
    size: int | None # Bytes, None for the rest of disk.
    type: str # GPT partition type GUID.
    mount_point: str | None # None for swap and BIOS boot.
    filesystem: str | None # fstab type.
    format_command: str | None
    spec: PartitionSpec | None = None

_MOUNT_POINT = re.compile(r"^/([A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*)?$")

def default_layout(disk: TargetDisk, uefi: bool, memory: int, architecture: str,
                   table: PartitionTable = PartitionTable.GPT) -> list[PartitionSpec]:
    """EFI system (UEFI) or BIOS boot partition (x86 BIOS with GPT), /boot, swap (size of memory, up to 8 GiB), root
    and /home. Small disks, and MBR tables without space for more partitions, get no /home, root uses remaining space
    then."""
    layout = []
    if uefi:
        layout.append(PartitionSpec(PartitionType.EFI, 1 * GIB, mount_point="/efi"))
    elif architecture in ("x86_64", "i686", "i586", "i486") and table == PartitionTable.GPT:
        layout.append(PartitionSpec(PartitionType.BIOS_BOOT, 1 * MIB))
    layout.append(PartitionSpec(PartitionType.LINUX, 1 * GIB, TargetFilesystem.EXT4, "/boot"))
    swap = min(8 * GIB, max(1 * GIB, round(memory / GIB) * GIB)) if memory else 2 * GIB
    disk_size = min(disk.size, MBR_MAX_SIZE) if table == PartitionTable.MBR else disk.size
    available = disk_size - sum(partition.size for partition in layout) - 8 * MIB
    if available - swap < 24 * GIB:
        swap = 0 if available < 16 * GIB else min(swap, 2 * GIB)
    if swap:
        layout.append(PartitionSpec(PartitionType.SWAP, swap))
    available -= swap
    free_slots = MBR_MAX_PARTITIONS - len(layout) if table == PartitionTable.MBR else None
    if available >= 96 * GIB and (free_slots is None or free_slots >= 2):
        # Root gets 30% of space (between 48 and 256 GiB), the rest is for /home.
        root = min(256 * GIB, max(48 * GIB, int(available * 0.3) // GIB * GIB))
        layout.append(PartitionSpec(PartitionType.LINUX, root, TargetFilesystem.EXT4, "/"))
        layout.append(PartitionSpec(PartitionType.LINUX, None, TargetFilesystem.EXT4, "/home"))
    else:
        layout.append(PartitionSpec(PartitionType.LINUX, None, TargetFilesystem.EXT4, "/"))
    return layout

@dataclass
class PartitionPlan:
    disk: TargetDisk
    uefi: bool
    layout: list[PartitionSpec] = field(default_factory=list)
    table: PartitionTable = PartitionTable.GPT

    @property
    def partitions(self) -> list[TargetPartition]:
        partitions = []
        for spec in self.layout:
            filesystem = spec.effective_filesystem
            fstab_type = "swap" if spec.type == PartitionType.SWAP else filesystem.value if filesystem else None
            if spec.type == PartitionType.SWAP:
                command = "mkswap -L swap"
            elif filesystem:
                command = filesystem.format_command(spec.label)
            else:
                command = None
            mount_point = spec.mount_point if spec.type in (PartitionType.LINUX, PartitionType.EFI) else None
            partition_type = spec.type.gpt_type if self.table == PartitionTable.GPT else spec.type.mbr_type
            partitions.append(TargetPartition(spec.label, spec.size, partition_type, mount_point, fstab_type, command, spec))
        return partitions

    @property
    def fixed_size(self) -> int:
        return sum(spec.size for spec in self.layout if spec.size)

    @property
    def usable_size(self) -> int:
        """Disk size without partition table and alignment. MBR addresses only first 2 TiB."""
        size = min(self.disk.size, MBR_MAX_SIZE) if self.table == PartitionTable.MBR else self.disk.size
        return size - 4 * MIB

    @property
    def remaining_size(self) -> int:
        return self.usable_size - self.fixed_size

    def size_of(self, spec: PartitionSpec) -> int:
        return spec.size if spec.size else max(0, self.remaining_size)

    @property
    def efi_mount_point(self) -> str | None:
        return next((spec.mount_point for spec in self.layout if spec.type == PartitionType.EFI and spec.mount_point), None)

    def index_of(self, predicate) -> int | None:
        """Partition number (1-based) of first partition matching predicate."""
        return next((index for index, spec in enumerate(self.layout, start=1) if predicate(spec)), None)

    def error(self) -> str | None:
        if not self.layout:
            return "Add partitions"
        roots = [spec for spec in self.layout if spec.type == PartitionType.LINUX and spec.mount_point == "/"]
        if len(roots) != 1:
            return "Exactly one Linux filesystem partition must be mounted at /"
        mount_points = [spec.mount_point for spec in self.layout if spec.type in (PartitionType.LINUX, PartitionType.EFI)]
        for mount_point in mount_points:
            if not mount_point:
                return "Set mount point of every EFI system and Linux filesystem partition"
            if not _MOUNT_POINT.match(mount_point) or "/.." in mount_point or "/./" in mount_point:
                return f"Invalid mount point {mount_point}"
        if len(set(mount_points)) != len(mount_points):
            return "Mount points must be different"
        for index, spec in enumerate(self.layout):
            if spec.size is None and index != len(self.layout) - 1:
                return "Only last partition can use remaining space"
            if spec.size is not None and spec.size < 1 * MIB:
                return "Partitions must have at least 1 MiB"
        if self.remaining_size < (1 * GIB if self.layout[-1].size is None else 0):
            return f"Partitions don't fit on disk, {format_size(self.usable_size)} available"
        efi = [spec for spec in self.layout if spec.type == PartitionType.EFI]
        if self.uefi and not efi:
            return "UEFI machine needs EFI system partition"
        if len(efi) > 1:
            return "Only one EFI system partition can be created"
        if any(spec.size and spec.size < 64 * MIB for spec in efi):
            return "EFI system partition needs at least 64 MiB"
        if self.table == PartitionTable.MBR:
            if len(self.layout) > MBR_MAX_PARTITIONS:
                return f"MBR table can have up to {MBR_MAX_PARTITIONS} partitions, use GPT for more"
            if any(spec.type == PartitionType.BIOS_BOOT for spec in self.layout):
                return "BIOS boot partition is used only with GPT"
        return None

    def warnings(self, architecture: str) -> list[str]:
        warnings = []
        if self.table == PartitionTable.MBR and self.disk.size > MBR_MAX_SIZE:
            warnings.append(f"MBR uses only first {format_size(MBR_MAX_SIZE)} of disk")
        if (self.table == PartitionTable.GPT and not self.uefi and architecture in ("x86_64", "i686", "i586", "i486")
                and not any(spec.type == PartitionType.BIOS_BOOT for spec in self.layout)):
            warnings.append("GRUB on BIOS machine needs BIOS boot partition")
        root = next((spec for spec in self.layout if spec.mount_point == "/" and spec.type == PartitionType.LINUX), None)
        if root and self.size_of(root) < 16 * GIB:
            warnings.append("Root partition is small")
        return warnings

    def partition_device(self, index: int) -> str:
        """Device of partition (1-based), eg. /dev/sda1 or /dev/nvme0n1p1."""
        separator = "p" if re.search(r"\d$", self.disk.path) else ""
        return f"{self.disk.path}{separator}{index}"

    def sfdisk_script(self) -> str:
        lines = [f"label: {self.table.value}"]
        # On MBR, partition with /boot (or root) is marked active, older BIOSes boot only from it.
        bootable = None
        if self.table == PartitionTable.MBR:
            bootable = self.index_of(lambda spec: spec.mount_point == "/boot") or self.index_of(lambda spec: spec.mount_point == "/")
        for index, partition in enumerate(self.partitions, start=1):
            size = partition.size
            if size is None and self.table == PartitionTable.MBR and self.disk.size > MBR_MAX_SIZE:
                size = self.remaining_size # Remaining space would reach past addressable part of disk.
            fields = [f"size={size // MIB}MiB"] if size else []
            fields.append(f"type={partition.type}")
            if self.table == PartitionTable.GPT:
                fields.append(f'name="{partition.name}"')
            if index == bootable:
                fields.append("bootable")
            lines.append(", ".join(fields))
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
        # Parents are mounted before their subdirectories.
        mounted = sorted(
            ((index, partition) for index, partition in enumerate(self.partitions, start=1) if partition.mount_point),
            key=lambda item: item[1].mount_point.rstrip("/").count("/") if item[1].mount_point != "/" else 0
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
            elif partition.filesystem == "vfat":
                entry = f"{partition.mount_point} vfat defaults,noatime,umask=0077 0 2"
            else:
                entry = f"{partition.mount_point} {partition.filesystem} defaults,noatime 0 2"
            lines.append(f'echo "UUID=$(blkid -s UUID -o value {device}) {entry}" >> "$FSTAB"')
        lines.append('cat "$FSTAB"')
        return "\n".join(lines) + "\n"

    def required_tools(self) -> list[str]:
        tools = ["wipefs", "sfdisk", "blkid", "tar"]
        tools += sorted({partition.format_command.split()[0] for partition in self.partitions if partition.format_command})
        return tools
