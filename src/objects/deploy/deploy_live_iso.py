from __future__ import annotations
import hashlib, os, re, shutil, subprocess, sys, tarfile, urllib.request
from .multistage_process import MultiStageProcess, MultiStageProcessStage, MultiStageProcessStageState
from .ssh_connection import quote

# ------------------------------------------------------------------------------
# Live CD builds (ISO images) prepared for machines that don't boot them as they are.
#
# Raspberry Pi firmware doesn't boot ISO images (GRUB on EFI partition): it reads its configuration (config.txt),
# kernel, initramfs and device trees from FAT partition. New ISO is made from ISO built by catalyst: the same ISO 9660
# contents (live system squashfs, kernel, GRUB), with FAT partition for Raspberry Pi as first MBR partition and EFI
# partition (image of original ISO) as second one, so it still boots on UEFI machines. It's written to USB drive or SD
# card as it is (dd, Raspberry Pi Imager). Kernel started by Raspberry Pi firmware finds live system like when booted
# by GRUB: initramfs (genkernel cdroot) searches disks for ISO with livecd file and mounts squashfs from it.
#
# Device trees, overlays and firmware files of older models come from Raspberry Pi firmware repository, from release
# of the kernel built for ISO (raspberrypi-sources and firmware releases have the same date), verified with Manifest of
# Gentoo repository. They are downloaded once and kept in Cache of library.

RASPBERRY_PI_ISO_SUFFIX = "-raspberry-pi"
_FIRMWARE_ARCHIVE_URL = "https://github.com/raspberrypi/firmware/archive/{tag}.tar.gz"
_FIRMWARE_PACKAGES = ("sys-boot/raspberrypi-firmware", "sys-kernel/raspberrypi-image") # Their Manifests list archives.
_KERNEL_RELEASE = re.compile(r"_p(\d{8})-raspberrypi")
_REQUIRED_TOOLS = ("xorriso", "mkfs.vfat", "mcopy")
_RASPBERRY_PI_ARCHITECTURES = ("arm", "arm64")

def is_live_iso_build(build) -> bool:
    """Completed build of live CD with its ISO image."""
    from .project_build import StageBuildStatus
    return (build.status == StageBuildStatus.COMPLETED and bool(build.artifact) and build.artifact.endswith(".iso")
            and os.path.isfile(build.artifact_path or ""))

def raspberry_pi_kernels(build) -> list[str]:
    """Kernels of ISO built from Raspberry Pi kernel sources (from spec of build)."""
    spec = os.path.join(build.path or "", "stage.spec")
    try:
        with open(spec, encoding="utf-8") as file:
            text = file.read()
    except OSError:
        return []
    return re.findall(r"^boot/kernel/([^/\s]+)/sources:\s*\S*raspberrypi\S*", text, re.MULTILINE)

def supports_raspberry_pi_iso(project_directory, build) -> bool:
    """ISO with Raspberry Pi kernel, built for ARM: it can be prepared for Raspberry Pi firmware."""
    architecture = project_directory.get_architecture() if project_directory else None
    return (is_live_iso_build(build) and architecture is not None and architecture.value in _RASPBERRY_PI_ARCHITECTURES
            and bool(raspberry_pi_kernels(build)))

def raspberry_pi_iso_path(build) -> str:
    """ISO prepared for Raspberry Pi, next to ISO of build."""
    return os.path.join(build.path, build.artifact.removesuffix(".iso") + RASPBERRY_PI_ISO_SUFFIX + ".iso")

def _firmware_cache_directory(tag: str) -> str:
    from .repository import library_directory
    return os.path.join(library_directory(), "Cache", "raspberrypi-firmware", tag)

# ------------------------------------------------------------------------------
# Running tools: as user on Linux (only files are created), in virtual machine (as root) on other systems.

class _UserConnection:
    host = "this computer"

    def connect(self) -> str | None:
        return None

    def close(self):
        pass

    def is_alive(self) -> bool:
        return True

    def run(self, script: str, output_handler=print, process_holder: list | None = None) -> bool:
        process = subprocess.Popen(["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if process_holder is not None:
            process_holder.append(process)
        for line in process.stdout:
            output_handler(line.rstrip("\n"))
        return process.wait() == 0

def _connection(project_directory):
    if sys.platform.startswith("linux"):
        return _UserConnection()
    from .deploy_image import MachineRootConnection, image_machine
    return MachineRootConnection(image_machine(project_directory))

# ------------------------------------------------------------------------------
# Boot configuration.

def parse_grub_entry(grub_cfg: str) -> tuple[str, str, str | None]:
    """Kernel, its arguments and initramfs of first menu entry of GRUB configuration of ISO."""
    entry = grub_cfg.split("menuentry", 1)[1] if "menuentry" in grub_cfg else grub_cfg
    linux = re.search(r"^\s*linux\s+(\S+)(.*)$", entry, re.MULTILINE)
    if not linux:
        raise RuntimeError("Kernel not found in GRUB configuration of ISO")
    initrd = re.search(r"^\s*initrd\s+(\S+)", entry, re.MULTILINE)
    return linux.group(1), linux.group(2).strip(), initrd.group(1) if initrd else None

def raspberry_pi_config(kernel: str, initramfs: str | None) -> str:
    lines = [
        "# Created by Catalyst Lab: starts live system of this ISO.",
        "# See https://www.raspberrypi.com/documentation/computers/config_txt.html",
        "arm_64bit=1",
        f"kernel={kernel}",
    ]
    if initramfs:
        lines.append(f"initramfs {initramfs} followkernel")
    lines += ["dtparam=audio=on", "disable_overscan=1"]
    return "\n".join(lines) + "\n"

def raspberry_pi_cmdline(arguments: str) -> str:
    """Arguments of kernel from GRUB, with console on screen (and serial port) and delay letting USB disks appear before
    initramfs searches for live system."""
    words = arguments.split()
    extra = [] if any(word.startswith("console=") for word in words) else ["console=serial0,115200", "console=tty1"]
    extra += [] if any(word.startswith("scandelay") for word in words) else ["scandelay=3"]
    return " ".join(extra + words) + "\n"

# ------------------------------------------------------------------------------
# Process.

class RaspberryPiIsoCreation(MultiStageProcess):
    """Creates ISO for Raspberry Pi from live CD build (see top of module)."""

    def __init__(self, project_directory, build):
        self.project_directory = project_directory
        self.project_id = project_directory.id if project_directory else None
        self.build = build
        self.iso_path = build.artifact_path
        self.result_path = raspberry_pi_iso_path(build)
        self.work_directory = os.path.join(build.path, ".raspberry-pi-iso")
        self.connection = None
        self.kernel_name = None # Files in ISO, from GRUB configuration.
        self.kernel_arguments = ""
        self.initramfs_name = None
        self.firmware_directory = None
        self.log_path = os.path.join(build.path, "raspberry-pi-iso.log")
        self._log_file = None
        super().__init__(title="Raspberry Pi ISO")

    def setup_stages(self):
        self.stages.append(_StepReadIso(multistage_process=self))
        self.stages.append(_StepFirmware(multistage_process=self))
        self.stages.append(_StepBootPartition(multistage_process=self))
        self.stages.append(_StepWriteIso(multistage_process=self))

    def name(self) -> str:
        return os.path.basename(self.result_path)

    def write_log(self, line: str):
        try:
            if self._log_file is None:
                self._log_file = open(self.log_path, "w", encoding="utf-8", buffering=1)
            self._log_file.write(line + "\n")
        except OSError as e:
            print(f"Failed to write log: {e}")

    def complete_process(self, success: bool):
        shutil.rmtree(self.work_directory, ignore_errors=True)
        if not success:
            try:
                os.remove(self.result_path + ".tmp")
            except OSError:
                pass
        self.write_log(f"=== {'Created ' + self.result_path if success else 'Failed'}")
        if self._log_file:
            self._log_file.close()
            self._log_file = None
        if self.connection:
            try:
                self.connection.close()
            except Exception as e:
                print(f"Failed to close connection: {e}")

class _Step(MultiStageProcessStage):
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

class _StepReadIso(_Step):
    def __init__(self, multistage_process):
        super().__init__(name="Read ISO", description="Reads kernel, its arguments and EFI partition of ISO",
                         multistage_process=multistage_process)

    def run(self):
        process = self.multistage_process
        if not os.path.isfile(process.iso_path):
            raise RuntimeError("ISO of build doesn't exist anymore")
        process.connection = _connection(process.project_directory)
        if error := process.connection.connect():
            raise RuntimeError(error)
        tools = " ".join(_REQUIRED_TOOLS)
        self.remote(f"""
missing=""
for tool in {tools}; do command -v "$tool" > /dev/null || missing="$missing $tool"; done
[ -z "$missing" ] || {{ echo "Missing tools:$missing (packages xorriso, dosfstools and mtools)"; exit 1; }}
""", "Tools needed to create ISO are missing")
        shutil.rmtree(process.work_directory, ignore_errors=True)
        os.makedirs(process.work_directory)
        work, iso = quote(process.work_directory), quote(process.iso_path)
        self.remote(f"""set -e
xorriso -osirrox on -indev {iso} -extract /boot/grub/grub.cfg {work}/grub.cfg -extract /efi.img {work}/efi.img 2>&1 | grep -v '^xorriso : UPDATE' || true
test -f {work}/grub.cfg || {{ echo "ISO has no GRUB configuration (boot/grub/grub.cfg)"; exit 1; }}
test -f {work}/efi.img || {{ echo "ISO has no EFI image (efi.img)"; exit 1; }}
chmod -R u+rw {work}
""", "Failed to read ISO")
        with open(os.path.join(process.work_directory, "grub.cfg"), encoding="utf-8") as file:
            kernel, arguments, initramfs = parse_grub_entry(file.read())
        process.kernel_name, process.kernel_arguments, process.initramfs_name = kernel.lstrip("/"), arguments, (initramfs or "").lstrip("/") or None
        self.log(f"Kernel: {process.kernel_name}, initramfs: {process.initramfs_name}, arguments: {arguments}")
        # Kernel, initramfs and kernel configuration (it names kernel release) are copied to boot partition.
        boot = os.path.join(process.work_directory, "boot")
        os.makedirs(boot)
        names = [process.kernel_name] + ([process.initramfs_name] if process.initramfs_name else [])
        extracts = " ".join(f"-extract /{quote(name)} {quote(os.path.join(boot, os.path.basename(name)))}" for name in names)
        config = process.kernel_name + "-config"
        self.remote(f"""set -e
xorriso -osirrox on -indev {iso} {extracts} -extract /{quote(config)} {work}/kernel-config 2>&1 | grep -v '^xorriso : UPDATE' || true
chmod -R u+rw {work}
""", "Failed to read kernel of ISO")
        for name in names:
            if not os.path.isfile(os.path.join(boot, os.path.basename(name))):
                raise RuntimeError(f"{name} is missing in ISO")

class _StepFirmware(_Step):
    def __init__(self, multistage_process):
        super().__init__(name="Raspberry Pi boot files", description="Downloads device trees and firmware of kernel release",
                         multistage_process=multistage_process)

    def run(self):
        process = self.multistage_process
        tag = self._firmware_tag()
        directory = _firmware_cache_directory(tag)
        if os.path.isdir(os.path.join(directory, "boot")):
            self.log(f"Using boot files of firmware {tag} from cache")
        else:
            self._download(tag, directory)
        process.firmware_directory = os.path.join(directory, "boot")

    def _firmware_tag(self) -> str:
        """Release of firmware matching kernel of ISO (date of raspberrypi-sources release), newest release in Gentoo
        repository otherwise."""
        process = self.multistage_process
        try:
            with open(os.path.join(process.work_directory, "kernel-config"), encoding="utf-8") as file:
                header = file.read(4096)
            if match := _KERNEL_RELEASE.search(header):
                self.log(f"Kernel release {match.group(1)}")
                return f"1.{match.group(1)}"
        except OSError:
            pass
        tags = sorted(self._manifest_hashes())
        if not tags:
            raise RuntimeError("Release of kernel is not known and Gentoo repository has no Raspberry Pi firmware")
        self.log(f"Kernel release is not known, using newest firmware {tags[-1]}")
        return tags[-1]

    def _snapshot_path(self) -> str | None:
        """Snapshot used by build, or snapshot of project."""
        from .repository import Repository
        process = self.multistage_process
        name = (((process.build.details or {}).get("inputs") or {}).get("snapshot") or {}).get("file")
        snapshots = list(Repository.Snapshot.value)
        snapshot = next((item for item in snapshots if item.filename == name), None)
        snapshot = snapshot or (process.project_directory.get_snapshot() if process.project_directory else None)
        snapshot = snapshot or (max(snapshots, key=lambda item: item.date or 0) if snapshots else None)
        return snapshot.file_path() if snapshot else None

    def _manifest_hashes(self) -> dict[str, str]:
        """SHA512 of firmware archives by release, from Manifests of Gentoo repository."""
        hashes = {}
        snapshot = self._snapshot_path()
        if not snapshot:
            return hashes
        for package in _FIRMWARE_PACKAGES:
            result = subprocess.run(["unsquashfs", "-cat", snapshot, f"{package}/Manifest"], capture_output=True, text=True)
            for match in re.finditer(r"^DIST raspberrypi-firmware-(\S+)\.tar\.gz \d+ .*?SHA512 ([0-9a-f]+)", result.stdout, re.MULTILINE):
                hashes[match.group(1)] = match.group(2)
        return hashes

    def _download(self, tag: str, directory: str):
        expected = self._manifest_hashes().get(tag)
        url = _FIRMWARE_ARCHIVE_URL.format(tag=tag)
        self.log(f"Downloading {url}")
        os.makedirs(os.path.dirname(directory), exist_ok=True)
        archive = directory + ".tar.gz.part"
        digest = hashlib.sha512()
        try:
            with urllib.request.urlopen(url, timeout=60) as response, open(archive, "wb") as file:
                size = int(response.headers.get("Content-Length") or 0)
                received, reported = 0, -1
                while chunk := response.read(1024 * 1024):
                    if self._cancel_event.is_set():
                        raise RuntimeError("Cancelled")
                    file.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if size:
                        self._update_progress(received / size * 0.9)
                    elif (megabytes := received // (20 * 1024 * 1024)) != reported:
                        reported = megabytes
                        self.log(f"{received // (1024 * 1024)} MiB")
            if expected:
                if digest.hexdigest() != expected:
                    raise RuntimeError("Downloaded archive doesn't match checksum in Gentoo repository")
                self.log("Checksum matches Manifest of Gentoo repository")
            else:
                self.log(f"Warning: firmware {tag} is not in Gentoo repository, archive is not verified")
            # Only boot files are kept: device trees, overlays and firmware of models that need it (not kernels).
            temporary = directory + ".part"
            shutil.rmtree(temporary, ignore_errors=True)
            with tarfile.open(archive, "r:gz") as tar:
                for member in tar:
                    parts = member.name.split("/")
                    if len(parts) < 3 or parts[1] != "boot" or not (member.isfile() or member.isdir()):
                        continue
                    relative = "/".join(parts[2:])
                    if not relative or (len(parts) == 3 and re.match(r"kernel.*\.img$", parts[2])):
                        continue
                    target = os.path.join(temporary, "boot", relative)
                    if member.isdir():
                        os.makedirs(target, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with tar.extractfile(member) as source, open(target, "wb") as output:
                        shutil.copyfileobj(source, output)
            if not any(name.endswith(".dtb") for name in os.listdir(os.path.join(temporary, "boot"))):
                raise RuntimeError("Archive has no device trees")
            shutil.rmtree(directory, ignore_errors=True)
            os.rename(temporary, directory)
        finally:
            try:
                os.remove(archive)
            except OSError:
                pass

class _StepBootPartition(_Step):
    def __init__(self, multistage_process):
        super().__init__(name="Boot partition", description="Creates FAT partition read by Raspberry Pi firmware",
                         multistage_process=multistage_process)

    def run(self):
        process = self.multistage_process
        boot = os.path.join(process.work_directory, "boot")
        # Firmware files and device trees, then kernel files of ISO.
        for name in os.listdir(process.firmware_directory):
            source = os.path.join(process.firmware_directory, name)
            if os.path.isdir(source):
                shutil.copytree(source, os.path.join(boot, name))
            else:
                shutil.copy2(source, boot)
        kernel = os.path.basename(process.kernel_name)
        initramfs = os.path.basename(process.initramfs_name) if process.initramfs_name else None
        with open(os.path.join(boot, "config.txt"), "w", encoding="utf-8") as file:
            file.write(raspberry_pi_config(kernel, initramfs))
        with open(os.path.join(boot, "cmdline.txt"), "w", encoding="utf-8") as file:
            file.write(raspberry_pi_cmdline(process.kernel_arguments))
        self.log("config.txt:")
        for line in raspberry_pi_config(kernel, initramfs).splitlines():
            self.log(f"  {line}")
        self.log(f"cmdline.txt: {raspberry_pi_cmdline(process.kernel_arguments).strip()}")
        size = sum(os.path.getsize(os.path.join(root, name)) for root, _, names in os.walk(boot) for name in names)
        # FAT32 with 512 byte clusters: files fit with little waste, enough clusters for FAT32 from 64 MiB.
        kib = max(64 * 1024, (size * 12 // 10) // 1024 + 16 * 1024)
        image = os.path.join(process.work_directory, "boot.img")
        self.log(f"Boot files: {size // (1024 * 1024)} MiB, partition {kib // 1024} MiB")
        self.remote(f"""set -e
mkfs.vfat -F 32 -s 1 -n RPI-BOOT -C {quote(image)} {kib}
cd {quote(boot)}
export MTOOLS_SKIP_CHECK=1
mcopy -s -i {quote(image)} ./* ::/
""", "Failed to create boot partition")

class _StepWriteIso(_Step):
    def __init__(self, multistage_process):
        super().__init__(name="Create ISO", description="Writes ISO with boot partition for Raspberry Pi",
                         multistage_process=multistage_process)

    def run(self):
        process = self.multistage_process
        work = process.work_directory
        temporary = process.result_path + ".tmp"
        # Contents and El Torito boot of ISO are kept, boot configuration of Raspberry Pi is added in raspberry-pi folder
        # (xorriso writes new ISO only when its contents change). Partitions in MBR: FAT for Raspberry Pi first (its
        # firmware boots from it), EFI partition second (UEFI machines boot from USB drive with it).
        self.remote(f"""set -e
rm -f {quote(temporary)}
xorriso -indev {quote(process.iso_path)} -outdev {quote(temporary)} -boot_image any keep \\
    -map {quote(os.path.join(work, "boot", "config.txt"))} /raspberry-pi/config.txt \\
    -map {quote(os.path.join(work, "boot", "cmdline.txt"))} /raspberry-pi/cmdline.txt \\
    -append_partition 1 0x0c {quote(os.path.join(work, "boot.img"))} \\
    -append_partition 2 0xef {quote(os.path.join(work, "efi.img"))} \\
    -commit 2>&1 | grep -v '^xorriso : UPDATE' || true
test -s {quote(temporary)} || {{ echo "ISO was not created"; exit 1; }}
xorriso -indev {quote(temporary)} -report_system_area plain 2>/dev/null | grep -i "mbr partition" || true
chmod a+r {quote(temporary)}
""", "Failed to create ISO")
        os.replace(temporary, process.result_path)
        self.log(f"Created {process.result_path} ({os.path.getsize(process.result_path) // (1024 * 1024)} MiB)")
