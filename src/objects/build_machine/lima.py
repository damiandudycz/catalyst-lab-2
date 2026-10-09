from __future__ import annotations
import json, os, shutil, subprocess

# ------------------------------------------------------------------------------
# Lima (https://lima-vm.io) runs virtual machines used to build on systems other than Linux. On macOS it uses
# Virtualization.framework. Folders of Catalyst Lab are shared with machines at the same paths.

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

def run_limactl(arguments: list[str], output_handler, process_holder: list | None = None, home: str | None = None) -> bool:
    """Runs limactl with output passed to handler line by line."""
    path = limactl_path()
    if path is None:
        raise RuntimeError(lima_unavailable_reason())
    output_handler(f"$ limactl {' '.join(arguments)}")
    process = subprocess.Popen([path, *arguments, "--tty=false"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, text=True, errors="replace", bufsize=1, start_new_session=True,
                               env=lima_environment(home))
    if process_holder is not None:
        process_holder.append(process)
    for line in process.stdout:
        output_handler(_clean_log_line(line.rstrip("\n")))
    return process.wait() == 0

def _clean_log_line(line: str) -> str:
    """Lima logs as time="..." level=info msg="...", only message is shown."""
    if line.startswith("time=") and ' msg="' in line:
        message = line.split(' msg="', 1)[1]
        return message[:-1].replace('\\"', '"') if message.endswith('"') else message
    return line

def machine_configuration(cpus: int, memory_gib: int, shared_paths: list[str]) -> str:
    """Lima configuration (YAML) of build machine."""
    mounts = "\n".join(f'- location: "{path}"\n  writable: true' for path in shared_paths)
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
    #!/bin/sh
    set -eux
    apk add --no-cache bash coreutils findutils grep sed tar xz zstd bubblewrap util-linux util-linux-misc \\
        shadow-subids squashfs-tools python3 curl e2fsprogs
    # User of machine (same uid as on host) gets subordinate ids for files of toolsets.
    user=$(getent passwd | awk -F: '$3 >= 500 && $3 < 60000 {{ print $1; exit }}')
    grep -q "^$user:" /etc/subuid || echo "$user:100000:65536" >> /etc/subuid
    grep -q "^$user:" /etc/subgid || echo "$user:100000:65536" >> /etc/subgid
    install -d -o "$user" {MACHINE_DATA_DIRECTORY}
"""
