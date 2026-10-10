from __future__ import annotations
import json, os, shutil, signal, subprocess, sys, threading

# ------------------------------------------------------------------------------
# Lima (https://lima-vm.io) runs virtual machines used to build on systems other than Linux. On macOS it uses
# Virtualization.framework. Folders of Catalyst Lab are shared with machines at the same paths.

def virtual_machines_supported() -> bool:
    """Virtual machines are available only on macOS, where toolsets can't run on the computer itself. Elsewhere
    (Linux) UI for them is hidden.
    TODO: Virtual machines could be supported on Linux too, for systems where toolsets can't run without root
    (unprivileged user namespaces blocked, eg. by AppArmor on Ubuntu or hardened kernels), as alternative to the root
    helper. It needs machine configuration for Linux hosts (vmType: qemu with KVM instead of vz, and shared folders
    mount type working there, eg. virtiofs or 9p), install hint not mentioning Homebrew, and testing. Machines could
    then be offered on Linux only when rootless toolsets are not supported (rootless_toolset_unsupported_reason)."""
    return sys.platform == "darwin"

# GUI apps on macOS don't get PATH of shell, so Homebrew locations are checked too.
_LIMACTL_LOCATIONS = ["/opt/homebrew/bin/limactl", "/usr/local/bin/limactl"]

# Alpine image of machines. Linux kernel of Alpine supports everything needed to work without root (user namespaces,
# overlayfs, squashfs), and its packages provide bubblewrap and newuidmap.
LIMA_TEMPLATE = "template:_images/alpine-3.23"

# Data that needs Linux file ownership (extracted toolsets, sessions, caches in use) can't be in shared folders (they
# can't store owners and are case insensitive on macOS). It's kept in working space of machine: ext4 image file in
# shared folder, mounted here while some operation needs it and deleted when no operation uses it.
MACHINE_DATA_DIRECTORY = "/var/lib/catalystlab"

# Machine disk contains only Alpine with tools, data of Catalyst Lab is in working space and shared folders.
SYSTEM_DISK_GIB = 8

# Lima checks that socket paths in instance directories are shorter than this (UNIX_PATH_MAX of macOS).
_MAX_SOCKET_PATH = 104
_INSTANCE_NAME_EXAMPLE = "catalystlab-00000000"

def machines_directory() -> str:
    """Machines of Catalyst Lab, one folder for each (disks, configuration, logs, working space). Next to toolsets."""
    from .repository import Repository
    toolsets_location = os.path.realpath(os.path.expanduser(Repository.Settings.value.toolsets_location))
    return os.path.join(os.path.dirname(toolsets_location), "Machines")

def default_lima_home() -> str:
    """~/.lima in real home of user (not $HOME, which can be changed). Used by machines created before machines
    directory, and when its path is too long."""
    import pwd
    return os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".lima")

def lima_home() -> str:
    """Lima directory (LIMA_HOME) for new machines: machines directory, or ~/.lima when socket paths in machines
    directory would be too long."""
    import pwd
    directory = machines_directory()
    if len(os.path.join(directory, _INSTANCE_NAME_EXAMPLE, "ssh.sock.1234567890123456")) >= _MAX_SOCKET_PATH:
        print(f"Path of machines directory {directory} is too long for Lima, using ~/.lima")
        return default_lima_home()
    return directory

def lima_environment(home: str | None = None) -> dict:
    """Environment of limactl, with Lima directory of machine (or for new machines)."""
    environment = dict(os.environ)
    environment["LIMA_HOME"] = home or lima_home()
    os.makedirs(environment["LIMA_HOME"], exist_ok=True)
    return environment

def limactl_path() -> str | None:
    return shutil.which("limactl") or next((path for path in _LIMACTL_LOCATIONS if os.access(path, os.X_OK)), None)

def lima_unavailable_reason() -> str | None:
    """None when virtual machines can be created, otherwise reason why not."""
    if limactl_path() is None:
        return "Lima is not installed. Install it with Homebrew: brew install lima"
    return None

def list_instances(home: str | None = None) -> dict[str, dict]:
    """Lima instances by name, with their status (Running, Stopped...)."""
    path = limactl_path()
    if path is None:
        return {}
    try:
        output = subprocess.run([path, "list", "--json"], capture_output=True, text=True, timeout=30, env=lima_environment(home)).stdout
    except Exception as e:
        print(f"Failed to list Lima instances: {e}")
        return {}
    instances = {}
    for line in output.splitlines():
        try:
            instance = json.loads(line)
            instances[instance["name"]] = instance
        except (ValueError, KeyError):
            pass
    return instances

def run_limactl(arguments: list[str], output_handler, process_holder: list | None = None, home: str | None = None,
                timeout: float | None = None) -> bool:
    """Runs limactl with output passed to handler line by line. With timeout, limactl (and processes it started) is
    killed when it doesn't finish in time, and False is returned."""
    path = limactl_path()
    if path is None:
        raise RuntimeError(lima_unavailable_reason())
    output_handler(f"$ limactl {' '.join(arguments)}")
    process = subprocess.Popen([path, *arguments, "--tty=false"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, text=True, errors="replace", bufsize=1, start_new_session=True,
                               env=lima_environment(home))
    if process_holder is not None:
        process_holder.append(process)
    timed_out = threading.Event()
    def kill():
        timed_out.set()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    timer = threading.Timer(timeout, kill) if timeout else None
    if timer:
        timer.daemon = True
        timer.start()
    try:
        for line in process.stdout:
            output_handler(_clean_log_line(line.rstrip("\n")))
        result = process.wait()
    finally:
        if timer:
            timer.cancel()
    if timed_out.is_set():
        output_handler(f"limactl {arguments[0]} didn't finish in {int(timeout)} seconds")
        return False
    return result == 0

def kill_instance_processes(instance_name: str, output_handler, home: str | None = None):
    """Last resort when limactl can't stop instance: kills its host agent with processes it started (driver of virtual
    machine) and removes their pid and socket files, so instance is stopped."""
    directory = os.path.join(home or lima_home(), instance_name)
    for pid_file in ("ha.pid", "vz.pid"):
        try:
            with open(os.path.join(directory, pid_file), encoding="utf-8") as file:
                pid = int(file.read().strip())
        except (OSError, ValueError):
            continue
        output_handler(f"Killing process {pid} of {instance_name} ({pid_file})")
        subprocess.run(["pkill", "-KILL", "-P", str(pid)], capture_output=True)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    for name in ("ha.pid", "ha.sock", "vz.pid", "default_ep.sock", "default_fd.sock"):
        try:
            os.remove(os.path.join(directory, name))
        except OSError:
            pass

def run_as_root_in_instance(instance_name: str, script: str, output_handler, home: str | None = None) -> bool:
    """Runs shell script as root in running instance, output goes to handler line by line."""
    path = limactl_path()
    if path is None:
        raise RuntimeError(lima_unavailable_reason())
    process = subprocess.Popen([path, "shell", "--workdir", "/", instance_name, "sudo", "sh", "-c", script],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                               errors="replace", bufsize=1, start_new_session=True, env=lima_environment(home))
    for line in process.stdout:
        line = line.rstrip("\n")
        if not (line.startswith("time=") and "level=" in line): # Lima warnings.
            output_handler(line)
    return process.wait() == 0

def swap_activation_script(size_bytes: int) -> str:
    """Turns on swap on additional disk of machine (as root). Disk is found by its size, only empty disk without
    partitions, filesystem or mounts is used, so system disk is never formatted."""
    return f"""set -e
SIZE={size_bytes}
DEVICE=""
for NAME in $(lsblk -dnro NAME,TYPE | awk '$2 == "disk" {{ print $1 }}'); do
    [ "$(lsblk -dnbro SIZE "/dev/$NAME")" = "$SIZE" ] || continue
    [ "$(lsblk -dnro RO "/dev/$NAME")" = "0" ] || continue
    [ "$(lsblk -nro NAME "/dev/$NAME" | wc -l)" = "1" ] || continue # Has partitions.
    findmnt -rn -S "/dev/$NAME" > /dev/null && continue
    if command -v blkid > /dev/null; then
        TYPE=$(blkid -p -o value -s TYPE "/dev/$NAME" 2>/dev/null || true)
        PTTYPE=$(blkid -p -o value -s PTTYPE "/dev/$NAME" 2>/dev/null || true)
        [ -z "$PTTYPE" ] || continue
        [ -z "$TYPE" ] || [ "$TYPE" = swap ] || continue
    fi
    DEVICE="/dev/$NAME"
    break
done
[ -n "$DEVICE" ] || {{ echo "Swap disk was not found in machine"; exit 1; }}
grep -q "^$DEVICE " /proc/swaps && {{ echo "Swap is on $DEVICE"; exit 0; }}
mkswap -L catalystlab-swap "$DEVICE" > /dev/null
swapon "$DEVICE"
echo "Swap: $((SIZE / 1073741824)) GiB on $DEVICE"
"""

def _clean_log_line(line: str) -> str:
    """Lima logs as time="..." level=info msg="...", only message is shown."""
    if line.startswith("time=") and ' msg="' in line:
        message = line.split(' msg="', 1)[1]
        return message[:-1].replace('\\"', '"') if message.endswith('"') else message
    return line

def machine_configuration(cpus: int, memory_gib: int, shared_paths: list[str]) -> str:
    """Lima configuration (YAML) of build machine."""
    mounts = "\n".join(f'- location: "{path}"\n  writable: true' for path in shared_paths)
    indented_setup = "\n".join("    " + line for line in machine_setup_script().splitlines())
    return f"""minimumLimaVersion: 2.0.0
base:
- {LIMA_TEMPLATE}
vmType: vz
cpus: {cpus}
memory: {memory_gib}GiB
disk: {SYSTEM_DISK_GIB}GiB
containerd:
  system: false
  user: false
mounts:
{mounts}
provision:
- mode: system
  script: |
{indented_setup}
"""

# Version of machine setup, increased when setup script changes. Existing machines are updated when started.
MACHINE_SETUP_VERSION = 2
_MACHINE_SETUP_VERSION_FILE = "/etc/catalystlab-setup-version"

# QEMU user emulators, to build stages for other architectures (like on Linux hosts, binfmt_misc with F flag makes
# them work in chroots and namespaces).
_QEMU_ARCHITECTURES = "x86_64 i386 arm armeb aarch64 riscv64 ppc ppc64 ppc64le s390x mips mipsel mips64 mips64el sparc sparc64 alpha m68k loongarch64 hppa sh4"

def machine_setup_script() -> str:
    """Prepares machine (as root, idempotent): tools for working without root, subordinate ids of user, working space
    mount point and emulators of other architectures."""
    return f"""#!/bin/sh
set -eux
apk add --no-cache bash coreutils findutils grep sed tar xz zstd bubblewrap util-linux util-linux-misc \\
    shadow-subids squashfs-tools python3 curl e2fsprogs qemu-openrc
host=$(uname -m)
for arch in {_QEMU_ARCHITECTURES}; do
    [ "$arch" = "$host" ] || apk add --no-cache "qemu-$arch" || echo "qemu-$arch is not available"
done
rc-update add qemu-binfmt default
rc-service qemu-binfmt restart || rc-service qemu-binfmt start
# User of machine (same uid as on host) gets subordinate ids for files of toolsets.
user=$(getent passwd | awk -F: '$3 >= 500 && $3 < 60000 {{ print $1; exit }}')
grep -q "^$user:" /etc/subuid || echo "$user:100000:65536" >> /etc/subuid
grep -q "^$user:" /etc/subgid || echo "$user:100000:65536" >> /etc/subgid
mountpoint -q {MACHINE_DATA_DIRECTORY} || install -d -o "$user" {MACHINE_DATA_DIRECTORY}
echo {MACHINE_SETUP_VERSION} > {_MACHINE_SETUP_VERSION_FILE}
"""

def machine_setup_update_script() -> str:
    """Runs setup in existing machine when its version is older."""
    return f"""[ "$(cat {_MACHINE_SETUP_VERSION_FILE} 2>/dev/null || echo 0)" -ge {MACHINE_SETUP_VERSION} ] && exit 0
echo "Updating machine setup to version {MACHINE_SETUP_VERSION}"
echo {_shell_base64(machine_setup_script())} | base64 -d | sudo sh
"""

def _shell_base64(text: str) -> str:
    import base64
    return base64.b64encode(text.encode()).decode()

