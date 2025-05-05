import os
import subprocess
import tempfile
import uuid
import shutil
import stat
from pathlib import Path
from dataclasses import dataclass
from typing import List
from typing import Optional
from collections import namedtuple
from .environment import RuntimeEnv
from .hotfix_patching import PatchSpec, HotFix, apply_patch_and_store_for_isolated_system
from .root_helper_client import root_function

@dataclass
class BindMount:
    mount_path: str                 # Mount location inside the isolated environment
    host_path: str | None = None    # None if mount point is an empty dir from overlay # TODO: Add alternative toolset_path to reference directly in toolset_dir
    toolset_path: str | None = None # Host path relative to toolset path when used in run_isolated_system_command.
    store_changes: bool = False     # True if changes should be stored outside isolated env
    resolve_host_path: bool = True  # Whether to resolve path through runtime_env

def run_isolated_system_command(toolset_root: str, command_to_run: List[str], hot_fixes: Optional[List[HotFix]] = None, additional_bindings: Optional[List[BindMount]] = None):
    """Runs the given command in an isolated Linux environment with host tools mounted as read-only."""

    runtime_env = RuntimeEnv.current()

    _system_bindings = [ # System.
        BindMount(mount_path="/usr",   toolset_path="/usr"),
        BindMount(mount_path="/bin",   toolset_path="/bin"),
        BindMount(mount_path="/sbin",  toolset_path="/sbin"),
        BindMount(mount_path="/lib",   toolset_path="/lib"),
        BindMount(mount_path="/lib32", toolset_path="/lib32"),
        BindMount(mount_path="/lib64", toolset_path="/lib64"),
    ]
    _devices_bindings = [ # Devices.
        BindMount(mount_path="/dev/kvm", host_path="/dev/kvm"),
    ]
    _config_bindings = [ # Config.
        BindMount(mount_path="/etc", toolset_path="/etc"),
        BindMount(mount_path="/etc/resolv.conf", host_path="/etc/resolv.conf"), # Take resolv.conf directly from main system
    ]
    _working_bindings = [ # Working.
        BindMount(mount_path="/var", toolset_path="/var"),
        BindMount(mount_path="/tmp"), # Create empty tmp when running env
    ]
    # All bindings.
    bindings = ( _system_bindings + _config_bindings + _devices_bindings + _working_bindings + (additional_bindings or []) )

    # Map bindings using toolset_path to host_path.
    for bind in bindings:
        if bind.host_path and bind.toolset_path:
            raise ValueError(f"BindMount for mount_path '{bind.mount_path}' has both host_path and toolset_path set. Only one is allowed.")
        if bind.toolset_path:
            bind.host_path = os.path.join(toolset_root, bind.toolset_path.lstrip("/"))

    work_dir = Path(tempfile.mkdtemp(prefix="gentoo_toolset_spawn_"))
    try:
        OverlayPaths = namedtuple("OverlayPaths", ["upper", "work"])

        # Prepare work dirs:
        fake_root = os.path.join(work_dir, "fake_root")
        overlay_root = os.path.join(work_dir, "overlay")
        hotfixes_workdir = os.path.join(work_dir, "hotfixes") # Stores patched files if needed
        os.makedirs(fake_root, exist_ok=False)
        os.makedirs(overlay_root, exist_ok=False)
        for field in OverlayPaths._fields: # Creates upper and work subdirectories.
            os.makedirs(os.path.join(overlay_root, field), exist_ok=False)
        os.makedirs(hotfixes_workdir, exist_ok=False)

        # Collect required hotfix patched files and add to bindings:
        hotfix_patches = [fix.get_patch_spec for fix in (hot_fixes or [])]
        for patch in hotfix_patches:
            patched_file_path = apply_patch_and_store_for_isolated_system(runtime_env, toolset_root, hotfixes_workdir, patch)
            if patched_file_path is not None:
                # Convert patch file to BindMount structure
                patched_file_binding = BindMount(mount_path=patch.source_path, host_path=patched_file_path, resolve_host_path=False)
                bindings.append(patched_file_binding)

        # Name overlay entries using indexes to avoid overlaps.
        mapping_index=0

        # Creates entry in overlay that maps other directory.
        def create_overlay_map(mount_path: str) -> OverlayPaths:
            nonlocal mapping_index
            # Create directories for all fields in OverlayPaths with given mount_path (upper, work [lower is considered mapped directory])
            map_name=mount_path.replace("/", "_")
            values = {
                field: f"{overlay_root}/{field}/{mapping_index}{map_name}".replace("//", "/")
                for field in OverlayPaths._fields
            }
            for path in values.values():
                os.makedirs(path, exist_ok=False)
            mapping_index+=1
            return OverlayPaths(**values)
        # Creates entry in overlay for temp dir, without mapping other directory.
        def create_overlay_temp(mount_path: str) -> str:
            nonlocal mapping_index
            map_name=mount_path.replace("/", "_")
            overlay_mount_path=f"{overlay_root}/upper/{mapping_index}{map_name}".replace("//", "/")
            os.makedirs(overlay_mount_path, exist_ok=False)
            mapping_index+=1
            return overlay_mount_path

        # Bind files and directories specified in bindings inside fake_root:
        bind_options = []
        for binding in bindings:
            resolved_host_path = ( # Used to check if exists through current runtime env (works with flatpak env)
                None if binding.host_path is None else
                runtime_env.resolve_path_for_host_access(binding.host_path) if binding.resolve_host_path else binding.host_path
            )
            # Handle not existing host paths.
            if binding.host_path is not None and not os.path.exists(resolved_host_path):
                # Create in host is store_changes is set.
                if binding.store_changes:
                    print(f"Path {resolved_host_path} not found. Creating directory in host.")
                    os.makedirs(resolved_host_path)
                # Skip not existing bindings with host_path set:
                else:
                    print(f"Path {resolved_host_path} not found. Skipping binding.")
                    continue # or raise an error if that's preferred

            # Empty writable dirs:
            if binding.host_path is None:
                tmp_path = create_overlay_temp(binding.mount_path)
                bind_options.extend(["--bind", tmp_path, binding.mount_path])
                continue
            # Symlinks (keep as symlinks in isolated env):
            if resolved_host_path is not None and os.path.islink(resolved_host_path):
                target = os.readlink(resolved_host_path)
                fake_symlink_path = os.path.join(fake_root, binding.mount_path.lstrip("/"))
                os.makedirs(os.path.dirname(fake_symlink_path), exist_ok=True)
                os.symlink(target, fake_symlink_path)
                continue
            # Char devices:
            if stat.S_ISCHR(os.stat(resolved_host_path).st_mode):
                flag = "--bind" if binding.store_changes else "--ro-bind"
                bind_options.extend([flag, binding.host_path, binding.mount_path])
                continue
            # Standard files:
            if stat.S_ISREG(os.stat(resolved_host_path).st_mode):
                flag = "--bind" if binding.store_changes else "--ro-bind"
                bind_options.extend([flag, binding.host_path, binding.mount_path])
                continue
            # Directories:
            if stat.S_ISDIR(os.stat(resolved_host_path).st_mode):
                if binding.store_changes:
                    bind_options.extend(["--bind", binding.host_path, binding.mount_path])
                else:
                    overlay = create_overlay_map(binding.mount_path)
                    bind_options.extend([
                        "--overlay-src", binding.host_path,
                        "--overlay", overlay.upper, overlay.work, binding.mount_path
                    ])
                continue

        result = start_toolset_command._async(handler=lambda x: print(x), fake_root=fake_root, bind_options=bind_options, command_to_run=command_to_run)
        print(result)

    finally:
        # Clean workdir.
        #shutil.rmtree(work_dir, ignore_errors=True)
        pass

@root_function
def start_toolset_command(fake_root: str, bind_options, command_to_run):
    cmd_bwrap = [
        "bwrap",
        "--die-with-parent",
        "--cap-add", "CAP_DAC_OVERRIDE", "--cap-add", "CAP_SYS_ADMIN", "--cap-add", "CAP_FOWNER", "--cap-add", "CAP_SETGID",
        "--unshare-uts", "--unshare-ipc", "--unshare-pid", "--unshare-cgroup",
        #"--unshare-user", "--uid", "0", "--gid", "0", # Only add when running as user, not root (pkexec)
        "--hostname", "catalyst-lab",
        "--bind", fake_root, "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--setenv", "HOME", "/"
    ]
    exec_call = cmd_bwrap + bind_options + command_to_run

    sys.stdout.write("Hello, terminal!\n")
    sys.stdout.flush()  # Ensure it's written immediately

    try:
        # Capture the stdout and stderr
        result = subprocess.run(
            exec_call,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )

        # Return both stdout and stderr as strings
        output = result.stdout.decode()  # stdout captured
        error = result.stderr.decode()   # stderr captured

        # You can return either or both, for example:
        if result.returncode == 0:
            return output  # Return standard output
        else:
            return error   # Return error output if there was a failure

    except subprocess.CalledProcessError as e:
        return f"Error occurred: {e.stderr.decode()}"  # In case of a subprocess error

#run_isolated_system_command(
#    runtime_env=RuntimeEnv.current(),
#    toolset_root="/gentoo_stage3_root",
#    command_to_run=["/bin/bash"], # TODO: Add source /etc/profile and env-update
#    #hot_fixes=HotFix.catalyst_fixes,
#    additional_bindings=[
#        # For catalyst -s
#        BindMount(mount_path="/var/tmp/catalyst/snapshots", host_path="/home/damiandudycz/Snapshots", store_changes=True, resolve_host_path=False),
#        # For emerge --sync.
#        # Note: /var is already binded using overlay, which means changes are not stored in env. By adding specific directories separately
#        # we can make them store inside the toolset itself.
#        # It's also possible to bind them to some completly different directory using /host_path, so that they could be shared across different envs
#        # and even used to create snapshot this way.
#        # If doing so, we could skip news and log, and just store repos
#        BindMount(mount_path="/var/db/repos/gentoo", host_path="/home/damiandudycz/tmp/var/db/repos/gentoo", store_changes=True),
#        BindMount(mount_path="/var/lib/gentoo/news", host_path="/home/damiandudycz/tmp/var/lib/gentoo/news", store_changes=True),
#        BindMount(mount_path="/var/log", host_path="/home/damiandudycz/tmp/var/log", store_changes=True)
#    ]
#)

