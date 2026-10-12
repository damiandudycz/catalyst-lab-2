from __future__ import annotations
import gzip, json, os, re, shutil, socket, subprocess, sys, tempfile, threading, uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from .event_bus import EventBus

# ------------------------------------------------------------------------------
# Running builds in QEMU: live CD ISO images (booted by firmware of machine), and stages with generic kernel (stage
# kernel and initramfs started by QEMU, system in disk image made from stage archive). Display is served over VNC on
# local socket and shown in the app, serial console and QEMU monitor (QMP) use sockets too.
# Machine of the same architecture as this computer is accelerated (HVF on macOS, KVM on Linux), others are emulated.
# Kernels of devices QEMU doesn't emulate (Raspberry Pi, PS3) can't be run.

_QEMU_SEARCH_PATHS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin")
_FIRMWARE_DIRECTORIES = ("/opt/homebrew/share/qemu", "/usr/local/share/qemu", "/usr/share/qemu", "/usr/share/edk2",
                         "/usr/share/edk2-ovmf", "/usr/share/AAVMF")

@dataclass
class QemuMachine:
    """How QEMU runs machine of architecture."""
    binary: str
    machine: list[str]           # Machine type and its options.
    console: str                 # Serial console device of kernel.
    cpu_emulated: str            # CPU when emulated (accelerated machine uses host CPU).
    firmware: list[str] = field(default_factory=list) # UEFI firmware file names (first found is used), empty for BIOS.
    display: list[str] = field(default_factory=lambda: ["-device", "virtio-gpu-pci"])
    disk_interface: str = "virtio"
    host_architecture: str = ""  # platform.machine() of computers that run it accelerated.

_MACHINES = {
    "arm64": QemuMachine(binary="qemu-system-aarch64", machine=["-machine", "virt"], console="ttyAMA0", cpu_emulated="max",
                         firmware=["edk2-aarch64-code.fd", "AAVMF_CODE.fd", "QEMU_EFI.fd"], host_architecture="arm64|aarch64"),
    "amd64": QemuMachine(binary="qemu-system-x86_64", machine=["-machine", "q35"], console="ttyS0", cpu_emulated="max",
                         display=["-device", "virtio-vga"], host_architecture="x86_64|amd64"),
    "x86": QemuMachine(binary="qemu-system-i386", machine=["-machine", "pc"], console="ttyS0", cpu_emulated="max",
                       display=["-device", "VGA"], host_architecture=""),
}

def qemu_binary(name: str) -> str | None:
    return shutil.which(name) or next((os.path.join(path, name) for path in _QEMU_SEARCH_PATHS
                                       if os.access(os.path.join(path, name), os.X_OK)), None)

def install_hint() -> str:
    return "brew install qemu" if sys.platform == "darwin" else "install QEMU (app-emulation/qemu on Gentoo)"

def _firmware_path(names: list[str]) -> str | None:
    for directory in _FIRMWARE_DIRECTORIES:
        for name in names:
            if os.path.isfile(os.path.join(directory, name)):
                return os.path.join(directory, name)
    return None

# ------------------------------------------------------------------------------
# What can run.

class QemuBuildKind(Enum):
    ISO = auto()
    STAGE = auto()

@dataclass
class QemuSupport:
    kind: QemuBuildKind | None
    machine: QemuMachine | None
    kernel: str | None = None     # Stage kernel and initramfs, in /boot.
    initramfs: str | None = None
    reason: str | None = None     # Why build can't run.

    @property
    def available(self) -> bool:
        return self.reason is None

_KERNEL = re.compile(r"\./boot/((vmlinuz|vmlinux|Image)-?[^/]*)$")
_INITRAMFS = re.compile(r"\./boot/((initramfs|initrd)-?[^/]*)$")

def _stage_boot_files(build) -> tuple[list[str], list[str]]:
    """Kernels and initramfs images in /boot of stage, from listing of archive made by catalyst."""
    kernels, initramfs = [], []
    listing = (build.artifact_path or "") + ".CONTENTS.gz"
    if not os.path.isfile(listing):
        return kernels, initramfs
    with gzip.open(listing, "rt", encoding="utf-8", errors="replace") as file:
        for line in file:
            line = line.rstrip("\n").split(" -> ")[0]
            if match := _KERNEL.search(line):
                kernels.append(match.group(1))
            elif match := _INITRAMFS.search(line):
                initramfs.append(match.group(1))
    return kernels, initramfs

def qemu_support(project_directory, build) -> QemuSupport:
    """Whether build can run in QEMU, how, or why not."""
    from .project_build import StageBuildStatus
    from .deploy_live_iso import is_live_iso_build, raspberry_pi_kernels
    from .deploy_installation import is_deployable
    architecture = project_directory.get_architecture() if project_directory else None
    machine = _MACHINES.get(architecture.value) if architecture else None
    if build.status != StageBuildStatus.COMPLETED or not build.artifact or not os.path.isfile(build.artifact_path or ""):
        return QemuSupport(None, machine, reason="Build has no result")
    if is_live_iso_build(build):
        kind = QemuBuildKind.ISO
        if raspberry_pi_kernels(build):
            return QemuSupport(kind, machine, reason="Kernel of ISO is built for Raspberry Pi, QEMU can't emulate it")
        kernel = initramfs = None
    elif is_deployable(project_directory, build):
        kind = QemuBuildKind.STAGE
        kernels, images = _stage_boot_files(build)
        if not kernels:
            return QemuSupport(kind, machine, reason="Stage has no kernel in /boot")
        generic = [kernel for kernel in kernels if not re.search(r"raspberrypi|ps3", kernel)]
        if not generic:
            return QemuSupport(kind, machine, reason="Kernel of stage is built for device QEMU can't emulate (Raspberry Pi, PS3)")
        kernel = sorted(generic)[-1]
        version = re.sub(r"^(vmlinuz|vmlinux|Image)-?", "", kernel)
        initramfs = next((image for image in images if version and version in image), images[0] if images else None)
    else:
        return QemuSupport(None, machine, reason="Only stage3, stage4 and live CD builds can run")
    if machine is None:
        return QemuSupport(kind, None, reason=f"Running {architecture.value if architecture else 'this architecture'} in QEMU isn't supported yet")
    if qemu_binary(machine.binary) is None:
        return QemuSupport(kind, machine, reason=f"QEMU is not installed ({install_hint()})")
    if machine.firmware and _firmware_path(machine.firmware) is None and kind == QemuBuildKind.ISO:
        return QemuSupport(kind, machine, reason="UEFI firmware of QEMU is not found")
    return QemuSupport(kind, machine, kernel=kernel, initramfs=initramfs)

def accelerated(machine: QemuMachine) -> str | None:
    """Accelerator when machine has architecture of this computer."""
    import platform
    if not machine.host_architecture or not re.fullmatch(machine.host_architecture, platform.machine().lower()):
        return None
    if sys.platform == "darwin":
        return "hvf"
    if sys.platform.startswith("linux") and os.access("/dev/kvm", os.R_OK | os.W_OK):
        return "kvm"
    return None

# ------------------------------------------------------------------------------
# Disk image of stage (made once, next to build).

def stage_image_directory(build) -> str:
    return os.path.join(build.path, "qemu")

def stage_image_ready(build, support: QemuSupport) -> bool:
    directory = stage_image_directory(build)
    return all(os.path.isfile(os.path.join(directory, name)) for name in
               ["root.img", support.kernel] + ([support.initramfs] if support.initramfs else []))

def stage_image_script(archive: str, directory: str, size: int, kernel: str, initramfs: str | None, console: str) -> str:
    """Creates ext4 disk image with stage (as root): root password removed (login without password, machine is for
    testing), serial console enabled, root filesystem in fstab. Kernel and initramfs are copied next to it."""
    from .ssh_connection import quote
    from .deploy_installation import tar_extract_command
    image = quote(os.path.join(directory, "root.img"))
    boot_files = " ".join(quote(name) for name in [kernel] + ([initramfs] if initramfs else []))
    return f"""set -e
D={quote(directory)}
MNT=$(mktemp -d)
cleanup() {{ umount "$MNT" 2>/dev/null || true; rmdir "$MNT" 2>/dev/null || true; }}
trap cleanup EXIT
rm -f {image}
truncate -s {size} {image}
mkfs.ext4 -q -F -L root {image}
mount -o loop {image} "$MNT"
echo "Extracting stage"
{tar_extract_command(archive, "@ROOT@").replace("@ROOT@", '"$MNT"').replace(' -f -', ' -f ' + quote(archive))}
# Machine for testing: root logs in without password, on screen and serial console.
sed -i 's/^root:[^:]*:/root::/' "$MNT/etc/shadow"
echo "/dev/vda / ext4 noatime 0 1" > "$MNT/etc/fstab"
if [ -f "$MNT/etc/inittab" ] && ! grep -q "^s0:" "$MNT/etc/inittab"; then
    echo "s0:12345:respawn:/sbin/agetty -L 115200 {console} vt100" >> "$MNT/etc/inittab"
fi
for file in {boot_files}; do cp "$MNT/boot/$file" "$D/$file"; done
chmod a+r "$D"/*
echo "Disk image: $(du -h {image} | cut -f1) used of {size // (1024 ** 3)} GiB"
"""

# ------------------------------------------------------------------------------
# Running machine.

class QemuRunEvent(Enum):
    STATE_CHANGED = auto()
    SERIAL_OUTPUT = auto()

class QemuRun:
    """QEMU process of build, with sockets of display (VNC), serial console and monitor (QMP)."""

    running_runs: list[QemuRun] = []

    def __init__(self, project_directory, build, support: QemuSupport, memory_mib: int = 4096, cpus: int = 4):
        self.project_directory = project_directory
        self.build = build
        self.support = support
        self.memory_mib = memory_mib
        self.cpus = cpus
        self.event_bus = EventBus[QemuRunEvent]()
        self.process: subprocess.Popen | None = None
        # Socket paths are short (limit of 104 characters on macOS).
        self.directory = tempfile.mkdtemp(prefix="cl-qemu-", dir="/tmp")
        self.vnc_socket = os.path.join(self.directory, "vnc")
        self.serial_socket = os.path.join(self.directory, "serial")
        self.qmp_socket = os.path.join(self.directory, "qmp")
        self.ssh_port = _free_port()
        self.error: str | None = None
        self.output: list[str] = [] # QEMU own messages.
        self._serial: socket.socket | None = None

    @property
    def name(self) -> str:
        return self.build.stage_name

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def accelerator(self) -> str | None:
        return accelerated(self.support.machine)

    def command(self) -> list[str]:
        machine = self.support.machine
        accelerator = self.accelerator
        command = [qemu_binary(machine.binary), *machine.machine, "-m", str(self.memory_mib), "-smp", str(self.cpus),
                   "-name", self.build.stage_name]
        command += ["-accel", accelerator, "-cpu", "host"] if accelerator else ["-accel", "tcg", "-cpu", machine.cpu_emulated]
        if machine.firmware and (firmware := _firmware_path(machine.firmware)):
            command += ["-drive", f"if=pflash,format=raw,readonly=on,file={firmware}"]
        command += machine.display
        command += ["-device", "qemu-xhci", "-device", "usb-kbd", "-device", "usb-tablet"]
        command += ["-netdev", f"user,id=net,hostfwd=tcp:127.0.0.1:{self.ssh_port}-:22", "-device", "virtio-net-pci,netdev=net"]
        command += ["-device", "virtio-rng-pci"]
        if self.support.kind == QemuBuildKind.ISO:
            command += ["-drive", f"file={self.build.artifact_path},format=raw,readonly=on,media=cdrom,if=none,id=cd",
                        "-device", "virtio-scsi-pci", "-device", "scsi-cd,drive=cd"]
        else:
            directory = stage_image_directory(self.build)
            command += ["-drive", f"file={self.overlay},format=qcow2,if={machine.disk_interface}",
                        "-kernel", os.path.join(directory, self.support.kernel),
                        "-append", f"root=/dev/vda rw rootwait console=tty0 console={machine.console}"]
            if self.support.initramfs:
                command += ["-initrd", os.path.join(directory, self.support.initramfs)]
        command += ["-pidfile", os.path.join(self.directory, "pid")] # Machines left by stopped app are found by it.
        command += ["-vnc", f"unix:{self.vnc_socket}", "-display", "none",
                    "-chardev", f"socket,id=serial,path={self.serial_socket},server=on,wait=off", "-serial", "chardev:serial",
                    "-qmp", f"unix:{self.qmp_socket},server=on,wait=off"]
        return command

    @property
    def overlay(self) -> str:
        return os.path.join(self.directory, "disk.qcow2")

    def start(self):
        if self.support.kind == QemuBuildKind.STAGE:
            # Changes of system are discarded when machine stops: it runs on overlay of disk image.
            image = os.path.join(stage_image_directory(self.build), "root.img")
            subprocess.run([qemu_binary("qemu-img"), "create", "-q", "-f", "qcow2", "-F", "raw", "-b", image, self.overlay],
                           check=True)
        command = self.command()
        self.output.append("$ " + " ".join(command))
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        QemuRun.running_runs.append(self)
        threading.Thread(target=self._read_output, daemon=True).start()
        threading.Thread(target=self._read_serial, daemon=True).start()
        self.event_bus.emit(QemuRunEvent.STATE_CHANGED, self)

    def _read_output(self):
        for line in self.process.stdout:
            self.output.append(line.rstrip("\n"))
        code = self.process.wait()
        if code != 0 and not self.error:
            self.error = next((line for line in reversed(self.output) if line.strip()), f"QEMU exited with code {code}")
        if self in QemuRun.running_runs:
            QemuRun.running_runs.remove(self)
        shutil.rmtree(self.directory, ignore_errors=True)
        self.event_bus.emit(QemuRunEvent.STATE_CHANGED, self)

    def _read_serial(self):
        connection = _connect_unix(self.serial_socket, lambda: self.is_running)
        if connection is None:
            return
        self._serial = connection
        while True:
            try:
                data = connection.recv(4096)
            except OSError:
                break
            if not data:
                break
            self.event_bus.emit(QemuRunEvent.SERIAL_OUTPUT, data.decode("utf-8", errors="replace"))

    def send_serial(self, data: bytes):
        if self._serial is not None:
            try:
                self._serial.sendall(data)
            except OSError:
                pass

    # Monitor (QMP).

    def monitor(self, command: str, arguments: dict | None = None) -> dict | None:
        """Runs QMP command, None when machine doesn't answer."""
        try:
            return self._monitor(command, arguments)
        except (OSError, ValueError) as e:
            print(f"QEMU monitor command {command} failed: {e}")
            return None

    def _monitor(self, command: str, arguments: dict | None) -> dict | None:
        connection = _connect_unix(self.qmp_socket, lambda: self.is_running, attempts=5)
        if connection is None:
            return None
        with connection:
            reader = connection.makefile("r")
            reader.readline() # Greeting.
            for message in ({"execute": "qmp_capabilities"}, {"execute": command, **({"arguments": arguments} if arguments else {})}):
                connection.sendall((json.dumps(message) + "\n").encode())
                while True:
                    line = reader.readline()
                    if not line:
                        return None
                    response = json.loads(line)
                    if "return" in response or "error" in response:
                        break
            return response

    def power_off(self):
        """ACPI power button, system shuts down itself."""
        self.monitor("system_powerdown")

    def reset(self):
        self.monitor("system_reset")

    def send_ctrl_alt_delete(self):
        self.monitor("send-key", {"keys": [{"type": "qcode", "data": key} for key in ("ctrl", "alt", "delete")]})

    def stop(self):
        """Stops machine immediately: quit command of QEMU, process is terminated (and killed) when it doesn't end."""
        if not self.is_running:
            return
        process = self.process
        self.monitor("quit")
        def ensure_stopped():
            for signal_function in (None, process.terminate, process.kill):
                if signal_function:
                    signal_function()
                try:
                    process.wait(timeout=3)
                    return
                except subprocess.TimeoutExpired:
                    continue
        threading.Thread(target=ensure_stopped, daemon=True).start()

def stop_all_runs():
    """Stops machines of app (when it quits)."""
    for run in list(QemuRun.running_runs):
        try:
            run.stop()
        except Exception as e:
            print(f"Failed to stop QEMU: {e}")

def stop_orphaned_runs():
    """Stops machines left running by app that was killed or crashed (their folders in /tmp have QEMU pid)."""
    import glob, signal
    for directory in glob.glob("/tmp/cl-qemu-*"):
        try:
            with open(os.path.join(directory, "pid")) as file:
                pid = int(file.read().strip())
            if "qemu-system" in subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout:
                print(f"Stopping QEMU left by previous run ({pid})")
                os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass
        shutil.rmtree(directory, ignore_errors=True)

def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]

def _connect_unix(path: str, alive, attempts: int = 100) -> socket.socket | None:
    """Connects to socket of QEMU, waiting until it's created."""
    import time
    for _ in range(attempts):
        if not alive():
            return None
        try:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.connect(path)
            return connection
        except OSError:
            time.sleep(0.1)
    return None
