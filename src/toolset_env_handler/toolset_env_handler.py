import os
import subprocess
import tempfile
import uuid
from pathlib import Path
import shutil
from .environment import RuntimeEnv
import shutil
from typing import List

def run_isolated_system_command(runtime_env: RuntimeEnv, command_to_run: List[str]):
    """ Runs given command in an isolated linux environment, where tools from host system are binded as read only """
    """ This version is to be run by accessing system host tools """

    base = Path(tempfile.mkdtemp(prefix="gentoo_toolset_spawn_"))
    try:
        fake_root = os.path.join(base, "fake-root")
        os.makedirs(fake_root, exist_ok=False)

        # Overlay for write access bindings.
        overlay = os.path.join(base, "overlay")
        os.makedirs(overlay, exist_ok=False)

        # There is an issue with commands that try to further isolate while being called in already isolated environment.
        # This patches one of libraries so that it ignores isolation call and just returns success.
        patched_namespaces_path = _disable_namespaces_setns_for_isolated_toolset(runtime_env, base)

        # TODO: Make system bindings rw when initializing new toolset environments, so that it can emerge tools.
        # Mount them from chroot dir then.
        host_bindings = [
            # Host path | # Mount path | # RW access | # Resolve /run/host
            ("/usr",      "/usr",        False,        True),
            ("/bin",      "/bin",        False,        True),
            ("/sbin",     "/sbin",       False,        True),
            ("/lib",      "/lib",        False,        True),
            ("/lib32",    "/lib32",      False,        True),
            ("/lib64",    "/lib64",      False,        True),
            ("/etc",      "/etc",        False,        True),
            ("/dev/kvm",  "/dev/kvm",    False,        True),
            #(None,        "/tmp",        True,         False), # Creates temporary working directories.
            (None,        "/var",        True,         False), # When host path is none, create it's directory inside temp base. Use this kind of binding when you need to enable write access to some working paths
            ("/home/damiandudycz/Snapshots", "/var/tmp/catalyst/snapshots", True, False),
        ]

        # Prepare bind options and directories for bwrap.
        bind_options = []
        for host_path, mount_path, rw, resolve_path in host_bindings:
            if host_path is None:
                # Create an empty directory inside the temporary overlay
                empty_dir = os.path.join(overlay, mount_path.lstrip("/"))
                os.makedirs(empty_dir, exist_ok=True)
                bind_options.extend([
                    "--bind" if rw else "--ro-bind",
                    empty_dir, mount_path
                ])
            else:
                resolved_path = (
                    runtime_env.resolve_path_for_host_access(host_path)
                    if resolve_path else host_path
                )
                if os.path.exists(resolved_path):
                    bind_options.extend([
                        "--bind" if rw else "--ro-bind",
                        host_path, mount_path
                    ])

        bwrap_cmd = [
            "flatpak-spawn", "--host", "bwrap",
            "--unshare-user", "--uid", "0", "--gid", "0",
            "--unshare-uts", "--unshare-ipc", "--unshare-pid", "--unshare-cgroup",
            "--hostname", "catalyst-lab",
            "--bind", fake_root, "/",
            "--dev", "/dev",
            "--proc", "/proc",
            # Bind selected files and directories:
            *bind_options,
            # Bind patched files.
            "--ro-bind", patched_namespaces_path, "/usr/lib/python3.12/site-packages/snakeoil/process/namespaces.py",
            "--setenv", "HOME", "/",
            *command_to_run
        ]

        subprocess.run(bwrap_cmd)

    finally:
        shutil.rmtree(base, ignore_errors=True)

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

