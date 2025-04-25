import os
import subprocess
import tempfile
import uuid
from pathlib import Path
import shutil
from dataclasses import dataclass
from typing import List
from .environment import RuntimeEnv
from .hotfix_patching import PatchSpec, HotFix, apply_patch_and_store_for_isolated_system
from typing import Optional
from collections import namedtuple

@dataclass
class BindMount:
    mount_path: str                               # Mount location inside the isolated environment
    host_path: str | None = None                  # None if mount point is an empty dir from overlay
    store_changes: bool = False                   # True if changes should be stored outside isolated env
    resolve_host_path: bool = True                # Whether to resolve path through runtime_env

def run_isolated_system_command(runtime_env: RuntimeEnv, toolset_root: str, command_to_run: List[str], hot_fixes: Optional[List[HotFix]] = None, additional_bindings: Optional[List[BindMount]] = None):
    """Runs the given command in an isolated Linux environment with host tools mounted as read-only."""

    _system_bindings = [ # System-related bindings (read-only)
        BindMount(mount_path="/usr",   host_path=f"{toolset_root}/usr"),
        BindMount(mount_path="/bin",   host_path=f"{toolset_root}/bin"),
        BindMount(mount_path="/sbin",  host_path=f"{toolset_root}/sbin"),
        BindMount(mount_path="/lib",   host_path=f"{toolset_root}/lib"),
        BindMount(mount_path="/lib32", host_path=f"{toolset_root}/lib32"),
        BindMount(mount_path="/lib64", host_path=f"{toolset_root}/lib64"),
        BindMount(mount_path="/var",   host_path=f"{toolset_root}/var"),
    ]
    _config_bindings = [ # Config bindings
        BindMount(mount_path="/etc", host_path=f"{toolset_root}/etc"),
    ]
    _devices_bindings = [ # Devices.
        BindMount(mount_path="/dev/kvm", host_path=f"{toolset_root}/dev/kvm"),
    ]
    _working_bindings = [ # Writable temporary overlays (temp and var)
        BindMount(mount_path="/tmp"),
    ]

    # Prepare required hotfix patches
    hotfix_patches = [fix.get_patch_spec for fix in (hot_fixes or [])]
    # Prepare bindings
    bindings = ( _system_bindings + _config_bindings + _devices_bindings + _working_bindings + (additional_bindings or []) )

    base = Path(tempfile.mkdtemp(prefix="gentoo_toolset_spawn_"))
    try:
        overlay = os.path.join(base, "overlay") # Stores changes in empty creates work dirs
        os.makedirs(overlay, exist_ok=False)
        os.makedirs(overlay + "/upper", exist_ok=False)
        os.makedirs(overlay + "/lower", exist_ok=False)
        os.makedirs(overlay + "/work",  exist_ok=False)

        hotfixes = os.path.join(base, "hotfixes") # Stores patched files if needed
        os.makedirs(hotfixes, exist_ok=False)

        for patch in hotfix_patches:
            patched_file_path = apply_patch_and_store_for_isolated_system(runtime_env, toolset_root, hotfixes, patch)
            if patched_file_path is not None:
                # Convert patch file to BindMount structure
                patched_file_binding = BindMount(mount_path=patch.source_path, host_path=patched_file_path, resolve_host_path=False)
                bindings.append(patched_file_binding)

        OverlayPaths = namedtuple("OverlayPaths", ["upper", "lower", "work"])

        def create_overlay(overlay_path: str, mount_path: str) -> OverlayPaths:
            sub_overlay_upper = (overlay_path + "/upper/" + mount_path).replace("//", "/")
            sub_overlay_lower = (overlay_path + "/lower/" + mount_path).replace("//", "/")
            sub_overlay_work  = (overlay_path + "/work/"  + mount_path).replace("//", "/")
            os.makedirs(sub_overlay_upper, exist_ok=False)
            os.makedirs(sub_overlay_lower, exist_ok=False)
            os.makedirs(sub_overlay_work,  exist_ok=False)
            return OverlayPaths(sub_overlay_upper, sub_overlay_lower, sub_overlay_work)

        bind_options = []
        for binding in bindings:
            if binding.host_path is None:
                # Mount an empty writable directory
                bind_path = create_overlay(overlay, binding.mount_path)
                bind_options.extend(["--bind", bind_path.work, binding.mount_path])
            else:
                resolved_path = (
                    runtime_env.resolve_path_for_host_access(binding.host_path)
                    if binding.resolve_host_path else binding.host_path
                )
                if os.path.exists(resolved_path):
                    if os.path.isfile(resolved_path):
                        # For files, always use --bind/--ro-bind
                        bind_options.extend([
                            "--bind" if binding.store_changes else "--ro-bind",
                            binding.host_path,
                            binding.mount_path
                        ])
                    elif os.path.isdir(resolved_path):
                        if binding.store_changes:
                            # Persistent bind for directories
                            bind_options.extend(["--bind", binding.host_path, binding.mount_path])
                        else:
                            # Overlay for directories
                            bind_path = create_overlay(overlay, binding.mount_path)
                            bind_options.extend([
                                "--overlay-src", binding.host_path,
                                "--overlay", bind_path.upper, bind_path.lower, binding.mount_path
                            ])

        bwrap_cmd = [
            "flatpak-spawn", "--host", "bwrap",
            "--die-with-parent",
            "--unshare-user", "--uid", "0", "--gid", "0",
            #"--cap-add", "CAP_DAC_OVERRIDE", "--cap-add", "CAP_SYS_ADMIN", "--cap-add", "CAP_FOWNER",
            "--unshare-uts", "--unshare-ipc", "--unshare-pid", "--unshare-cgroup",
            "--hostname", "catalyst-lab",
            "--bind", overlay + "/work", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--setenv", "HOME", "/",
        ] + bind_options + command_to_run

        print(bwrap_cmd)

        subprocess.run(bwrap_cmd)

    finally:
        print("done")
        #shutil.rmtree(base, ignore_errors=True)

