import os
import subprocess
import tempfile
import uuid
from pathlib import Path
import shutil
from dataclasses import dataclass
from typing import List
from .environment import RuntimeEnv

@dataclass
class BindMount:
    mount_path: str                # Mount location inside the isolated environment
    host_path: str | None = None   # None if mount point is an empty dir from overlay
    write_access: bool = False     # True if writable
    resolve_host_path: bool = True # Whether to resolve path through runtime_env

_system_bindings = [ # System-related bindings (read-only)
    BindMount(mount_path="/usr",   host_path="/usr"),
    BindMount(mount_path="/bin",   host_path="/bin"),
    BindMount(mount_path="/sbin",  host_path="/sbin"),
    BindMount(mount_path="/lib",   host_path="/lib"),
    BindMount(mount_path="/lib32", host_path="/lib32"),
    BindMount(mount_path="/lib64", host_path="/lib64"),
]
_config_bindings = [ # Config bindings
    BindMount(mount_path="/etc", host_path="/etc"),
]
_devices_bindings = [ # Devices
    BindMount(mount_path="/dev/kvm", host_path="/dev/kvm"),
]
_working_bindings = [ # Writable overlays (temp and var)
    BindMount(mount_path="/tmp", write_access=True, resolve_host_path=False),
    BindMount(mount_path="/var", write_access=True, resolve_host_path=False),
]

def run_isolated_system_command(runtime_env: RuntimeEnv, command_to_run: List[str]):
    """Runs the given command in an isolated Linux environment with host tools mounted as read-only."""

    # TODO: Make this dynamic or a parameter
    files_bindings = [
        BindMount(mount_path="/var/tmp/catalyst/snapshots", host_path="/home/damiandudycz/Snapshots", write_access=True, resolve_host_path=False),
    ]

    bindings = ( _system_bindings + _config_bindings + _devices_bindings + _working_bindings + files_bindings )

    base = Path(tempfile.mkdtemp(prefix="gentoo_toolset_spawn_"))
    try:
        fake_root = os.path.join(base, "fake-root")
        os.makedirs(fake_root, exist_ok=False)

        overlay = os.path.join(base, "overlay")
        os.makedirs(overlay, exist_ok=False)

        # Patch isolation-related code in user-space libs
        patched_namespaces_path = _disable_namespaces_setns_for_isolated_toolset(runtime_env, base)

        bind_options = []
        for binding in bindings:
            if binding.host_path is None:
                # Mount an empty writable directory
                empty_dir = os.path.join(overlay, binding.mount_path.lstrip("/"))
                os.makedirs(empty_dir, exist_ok=True)
                bind_options.extend([
                    "--bind" if binding.write_access else "--ro-bind",
                    empty_dir,
                    binding.mount_path
                ])
            else:
                resolved_path = (
                    runtime_env.resolve_path_for_host_access(binding.host_path)
                    if binding.resolve_host_path else binding.host_path
                )
                if os.path.exists(resolved_path):
                    bind_options.extend([
                        "--bind" if binding.write_access else "--ro-bind",
                        binding.host_path,
                        binding.mount_path
                    ])

        bwrap_cmd = [
            "flatpak-spawn", "--host", "bwrap",
            "--unshare-user", "--uid", "0", "--gid", "0",
            "--unshare-uts", "--unshare-ipc", "--unshare-pid", "--unshare-cgroup",
            "--hostname", "catalyst-lab",
            "--bind", fake_root, "/",
            "--dev", "/dev",
            "--proc", "/proc",
            *bind_options,
            "--ro-bind", patched_namespaces_path,
            "/usr/lib/python3.12/site-packages/snakeoil/process/namespaces.py",
            "--setenv", "HOME", "/",
            *command_to_run
        ]

        subprocess.run(bwrap_cmd)

    finally:
        shutil.rmtree(base, ignore_errors=True)


# Hot fixes / patches:

def _disable_namespaces_setns_for_isolated_toolset(runtime_env: RuntimeEnv, temp_dir: str):
    """ This function disables setns function in snakeoil/process/namespaces.py library """
    """ This is needed to avoid issue with nested unshare when using flatpak-spawn and catalyst """
    """ WARNING! This code is potentially unsafe and might cause some issues """
    original_namespaces_path = runtime_env.resolve_path_for_host_access('/usr/lib/python3.12/site-packages/snakeoil/process/namespaces.py')
    with open(original_namespaces_path, 'r') as file:
        lines = file.readlines()

    func_to_disable = "def setns(fd, nstype):"
    patched_lines = []
    skip_block = False
    inside_setns = False

    for line in lines:
        stripped = line.strip()

        if not skip_block and stripped.startswith(func_to_disable):
            patched_lines.append(f"{func_to_disable}\n")
            patched_lines.append("    pass\n\n")
            skip_block = True
            inside_setns = True
            continue

        if skip_block:
            if line.startswith(" ") or line.strip() == "":
                continue
            else:
                skip_block = False

        if not skip_block:
            patched_lines.append(line)

    patched_path = os.path.join(temp_dir, "namespaces.py")
    with open(patched_path, 'w') as file:
        file.writelines(patched_lines)

    return patched_path

#run_isolated_system_flatpak_command(command_to_run=["/usr/bin/catalyst", "-s", "stable"])

