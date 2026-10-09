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

# Data that needs Linux file ownership (extracted toolsets, sessions, caches) is kept on disk of machine, shared
# folders can't store owners and are case insensitive on macOS.
MACHINE_DATA_DIRECTORY = "/var/lib/catalystlab"

def lima_environment() -> dict:
    """Environment of limactl. Lima directory is in real home of user (not $HOME, which can be changed, eg. for tests),
    as paths of its sockets are limited to 104 characters."""
    import pwd
    environment = dict(os.environ)
    environment.setdefault("LIMA_HOME", os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".lima"))
    return environment

def limactl_path() -> str | None:
    return shutil.which("limactl") or next((path for path in _LIMACTL_LOCATIONS if os.access(path, os.X_OK)), None)

def lima_unavailable_reason() -> str | None:
    """None when virtual machines can be created, otherwise reason why not."""
    if limactl_path() is None:
        return "Lima is not installed. Install it with Homebrew: brew install lima"
    return None

def list_instances() -> dict[str, dict]:
    """Lima instances by name, with their status (Running, Stopped...)."""
    path = limactl_path()
    if path is None:
        return {}
    try:
        output = subprocess.run([path, "list", "--json"], capture_output=True, text=True, timeout=30, env=lima_environment()).stdout
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

def run_limactl(arguments: list[str], output_handler, process_holder: list | None = None) -> bool:
    """Runs limactl with output passed to handler line by line."""
    path = limactl_path()
    if path is None:
        raise RuntimeError(lima_unavailable_reason())
    output_handler(f"$ limactl {' '.join(arguments)}")
    process = subprocess.Popen([path, *arguments, "--tty=false"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, text=True, errors="replace", bufsize=1, start_new_session=True,
                               env=lima_environment())
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

def machine_configuration(cpus: int, memory_gib: int, disk_gib: int, shared_paths: list[str]) -> str:
    """Lima configuration (YAML) of build machine."""
    mounts = "\n".join(f'- location: "{path}"\n  writable: true' for path in shared_paths)
    return f"""minimumLimaVersion: 2.0.0
base:
- {LIMA_TEMPLATE}
vmType: vz
cpus: {cpus}
memory: {memory_gib}GiB
disk: {disk_gib}GiB
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
        shadow-subids squashfs-tools python3 curl
    # User of machine (same uid as on host) gets subordinate ids for files of toolsets.
    user=$(getent passwd | awk -F: '$3 >= 500 && $3 < 60000 {{ print $1; exit }}')
    grep -q "^$user:" /etc/subuid || echo "$user:100000:65536" >> /etc/subuid
    grep -q "^$user:" /etc/subgid || echo "$user:100000:65536" >> /etc/subgid
    install -d -o "$user" {MACHINE_DATA_DIRECTORY}
"""
