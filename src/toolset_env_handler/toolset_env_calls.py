import os
import subprocess
import tempfile
import uuid
from pathlib import Path
import shutil
from dataclasses import dataclass
from typing import List
from .environment import RuntimeEnv
from .hotfix_patching import PatchSpec, apply_patch_and_store_for_isolated_system

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
    # Prepare required hotfix patches
    hotfix_patches = [
        PatchSpec( # This patch fakes unshare call making it possible to run catalyst inside isolated env.
            source_path="/usr/lib/python3.12/site-packages/snakeoil/process/namespaces.py",
            patch_filename="namespaces.patch"  # The patch file inside patches/
        )
    ]

    # TODO: Make this dynamic or a parameter
    files_bindings = [
        BindMount(mount_path="/var/tmp/catalyst/snapshots", host_path="/home/damiandudycz/Snapshots", write_access=True, resolve_host_path=False),
    ]

    bindings = ( _system_bindings + _config_bindings + _devices_bindings + _working_bindings + files_bindings )

    base = Path(tempfile.mkdtemp(prefix="gentoo_toolset_spawn_"))
    try:
        fake_root = os.path.join(base, "fake-root") # Base isolated system structure
        os.makedirs(fake_root, exist_ok=False)

        overlay = os.path.join(base, "overlay") # Stores changes in empty creates work dirs
        os.makedirs(overlay, exist_ok=False)

        hotfixes = os.path.join(base, "hotfixes") # Stores patched files if needed
        os.makedirs(hotfixes, exist_ok=False)

        for patch in hotfix_patches:
            patched_file_path = apply_patch_and_store_for_isolated_system(runtime_env, hotfixes, patch)
            # Convert patch file to BindMount structure
            patched_file_binding = BindMount(mount_path=patch.source_path, host_path=patched_file_path, resolve_host_path=False)
            bindings.append(patched_file_binding)

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
            "--setenv", "HOME", "/",
            *command_to_run
        ]

        subprocess.run(bwrap_cmd)

    finally:
        shutil.rmtree(base, ignore_errors=True)

#run_isolated_system_flatpak_command(command_to_run=["/usr/bin/catalyst", "-s", "stable"])

