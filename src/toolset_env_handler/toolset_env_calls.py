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
import stat

@dataclass
class BindMount:
    mount_path: str                               # Mount location inside the isolated environment
    host_path: str | None = None                  # None if mount point is an empty dir from overlay
    store_changes: bool = False                   # True if changes should be stored outside isolated env
    resolve_host_path: bool = True                # Whether to resolve path through runtime_env

def run_isolated_system_command(runtime_env: RuntimeEnv, toolset_root: str, command_to_run: List[str], hot_fixes: Optional[List[HotFix]] = None, additional_bindings: Optional[List[BindMount]] = None):
    """Runs the given command in an isolated Linux environment with host tools mounted as read-only."""

    _system_bindings = [ # System.
        BindMount(mount_path="/usr",   host_path=f"{toolset_root}/usr"),
        BindMount(mount_path="/bin",   host_path=f"{toolset_root}/bin"),
        BindMount(mount_path="/sbin",  host_path=f"{toolset_root}/sbin"),
        BindMount(mount_path="/lib",   host_path=f"{toolset_root}/lib"),
        BindMount(mount_path="/lib32", host_path=f"{toolset_root}/lib32"),
        BindMount(mount_path="/lib64", host_path=f"{toolset_root}/lib64"),
    ]
    _devices_bindings = [ # Devices.
        BindMount(mount_path="/dev/kvm", host_path="/dev/kvm"),
    ]
    _config_bindings = [ # Config.
        BindMount(mount_path="/etc", host_path=f"{toolset_root}/etc"),
    ]
    _working_bindings = [ # Working.
        BindMount(mount_path="/var", host_path=f"{toolset_root}/var"),
        BindMount(mount_path="/tmp"), # Create empty tmp when running env
    ]
    # All bindings.
    bindings = ( _system_bindings + _config_bindings + _devices_bindings + _working_bindings + (additional_bindings or []) )

    # NOTE: Paths work correctly with flatpak-spawn --host, because we use --filesystem=/tmp. This means that mappings that are rooted inside /tmp
    # in flatpak contained code still corresponds also to real host /tmp. Without this we would need to access path related to application container runtime
    # when using flatpak-spawn --host. It could be better approach to leave flatpak /tmp as isolated inside flatpak, but then we would need to get the real path of /tmp
    # related to app runtime container, in "--bind", fake_root, "/".
    work_dir = Path(tempfile.mkdtemp(prefix="gentoo_toolset_spawn_"))
    try:
        fake_root = os.path.join(work_dir, "fake_root")
        hotfixes_workdir = os.path.join(work_dir, "hotfixes") # Stores patched files if needed
        overlay_root = os.path.join(work_dir, "overlay")

        os.makedirs(fake_root, exist_ok=False)
        os.makedirs(hotfixes_workdir, exist_ok=False)
        os.makedirs(overlay_root, exist_ok=False)
        os.makedirs(os.path.join(overlay_root, "upper"), exist_ok=False)
        os.makedirs(os.path.join(overlay_root, "work"), exist_ok=False)

        # Collect required hotfix patches details.
        hotfix_patches = [fix.get_patch_spec for fix in (hot_fixes or [])]
        for patch in hotfix_patches:
            patched_file_path = apply_patch_and_store_for_isolated_system(runtime_env, toolset_root, hotfixes_workdir, patch)
            if patched_file_path is not None:
                # Convert patch file to BindMount structure
                patched_file_binding = BindMount(mount_path=patch.source_path, host_path=patched_file_path, resolve_host_path=False)
                bindings.append(patched_file_binding)

        OverlayPaths = namedtuple("OverlayPaths", ["upper", "work"])

        # Name overlay entries using indexes to avoid overlaps.
        mapping_index=0

        # Creates entry in overlay that maps other directory.
        def create_overlay_map(mount_path: str) -> OverlayPaths:
            nonlocal mapping_index
            # Create directories for all fields in OverlayPaths with given mount_path (upper, work [lower is considered mapped directory])
            values = {
                field: f"{overlay_root}/{field}/{mapping_index}".replace("//", "/")
                for field in OverlayPaths._fields
            }
            for path in values.values():
                os.makedirs(path, exist_ok=False)
            mapping_index+=1
            return OverlayPaths(**values)
        # Creates entry in overlay for temp dir, without mapping other directory.
        def create_overlay_temp(mount_path: str) -> str:
            nonlocal mapping_index
            overlay_mount_path=f"{overlay_root}/{mapping_index}".replace("//", "/")
            os.makedirs(overlay_mount_path, exist_ok=False)
            mapping_index+=1
            return overlay_mount_path

        bind_options = []
        for binding in bindings:
            resolved_path = ( # Used to check if exists through current runtime env (works with flatpak env)
                None if binding.host_path is None else
                runtime_env.resolve_path_for_host_access(binding.host_path) if binding.resolve_host_path else binding.host_path
            )
            # Empty writable dirs:
            if binding.host_path is None:
                tmp_path = create_overlay_temp(binding.mount_path)
                bind_options.extend(["--bind", tmp_path, binding.mount_path])
                continue
            # Skip not existing bindings with host_path set:
            if not os.path.exists(resolved_path):
                print(f"Path {resolved_path} not found. Skipping binding.")
                continue # or raise an error if that's preferred
            # Standard files:
            if os.path.isfile(resolved_path):
                flag = "--bind" if binding.store_changes else "--ro-bind"
                bind_options.extend([flag, binding.host_path, binding.mount_path])
                continue
            # Char devices:
            if os.path.exists(resolved_path) and stat.S_ISCHR(os.stat(resolved_path).st_mode):
                flag = "--bind" if binding.store_changes else "--ro-bind"
                bind_options.extend([flag, binding.host_path, binding.mount_path])
                continue
            # Directories:
            if os.path.isdir(resolved_path):
                if binding.store_changes:
                    bind_options.extend(["--bind", binding.host_path, binding.mount_path])
                else:
                    overlay = create_overlay_map(binding.mount_path)
                    bind_options.extend([
                        "--overlay-src", binding.host_path,
                        "--overlay", overlay.upper, overlay.work, binding.mount_path
                    ])
                continue

        # Add special prefix when running from flatpak
        prefix = ["flatpak-spawn", "--host"] if runtime_env == RuntimeEnv.FLATPAK else []

        exec_call = prefix + [
            "pkexec", "bwrap",
            "--die-with-parent",
            #"--unshare-user", "--uid", "0", "--gid", "0",
            "--cap-add", "CAP_DAC_OVERRIDE", "--cap-add", "CAP_SYS_ADMIN", "--cap-add", "CAP_FOWNER", "--cap-add", "CAP_SETGID",
            "--unshare-uts", "--unshare-ipc", "--unshare-pid", "--unshare-cgroup",
            "--hostname", "catalyst-lab",
            "--bind", fake_root, "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--setenv", "HOME", "/",
        ] + bind_options + command_to_run

        print(' '.join(str(x) for x in exec_call))
        subprocess.run(exec_call)

    finally:
        # Clean workdir
        shutil.rmtree(work_dir, ignore_errors=True)

run_isolated_system_command(
    runtime_env=RuntimeEnv.current(),
    toolset_root="",
    command_to_run=["/bin/bash"],
    hot_fixes=HotFix.catalyst_fixes,
    additional_bindings=[
        BindMount(mount_path="/var/tmp/catalyst/snapshots", host_path="/home/damiandudycz/Snapshots", store_changes=True, resolve_host_path=False)
    ]
)
