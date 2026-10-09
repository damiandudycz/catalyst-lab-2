from __future__ import annotations
import functools, os, re, shlex, shutil, signal, subprocess, tempfile, threading, time, uuid
from .repository import Repository

# ------------------------------------------------------------------------------
# Working without root privileges.
# Commands run as root of unprivileged user namespace, where ids 1..65536 are mapped to subordinate ids of user
# (/etc/subuid, /etc/subgid), so files can have their real owners (portage, shadow...). Things that need real root
# are replaced: squashfs files are extracted (loop mounts are not allowed) and host /dev is bound instead of creating
# device nodes. Files of namespace root are owned by user, other owners are mapped ids (100000+), so such files are
# created, packed and removed inside namespace.

MAPPED_IDS_COUNT = 65536
MIN_BWRAP_VERSION = (0, 11, 0) # Toolset bindings use --overlay-src and --overlay.

def rootless_directory() -> str:
    """Extracted toolsets and snapshots, distfiles, work folders and temporary sessions. Next to toolsets folder."""
    toolsets_location = os.path.realpath(os.path.expanduser(Repository.Settings.value.toolsets_location))
    return os.path.join(os.path.dirname(toolsets_location), ".rootless")

def is_rootless_path(path: str | None) -> bool:
    """Path is in rootless directory, its files are owned by user or mapped ids, not by real root."""
    if not path:
        return False
    directory = rootless_directory()
    path = os.path.realpath(path)
    return path.startswith(directory + os.sep)

@functools.cache
def rootless_toolset_unsupported_reason() -> str | None:
    """None when toolset environments (bwrap) can run without root, otherwise reason why not."""
    if reason := rootless_unsupported_reason():
        return reason
    path = shutil.which("bwrap")
    if path is None:
        return "bwrap is not installed"
    try:
        output = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10).stdout
        version = tuple(int(number) for number in re.findall(r"\d+", output)[:3])
    except Exception as e:
        return f"Failed to check bwrap version: {e}"
    if version < MIN_BWRAP_VERSION:
        return f"bwrap {'.'.join(map(str, MIN_BWRAP_VERSION))} or newer is required"
    return None

@functools.cache
def rootless_unsupported_reason() -> str | None:
    """None when commands can run without root, otherwise reason why not. Checked once while app runs."""
    if os.environ.get("CATALYSTLAB_ROOTLESS", "1") == "0":
        return "Disabled with CATALYSTLAB_ROOTLESS=0"
    for tool in ("unshare", "newuidmap", "newgidmap", "unsquashfs"):
        if shutil.which(tool) is None:
            return f"{tool} is not installed"
    if _subordinate_range("/etc/subuid") is None or _subordinate_range("/etc/subgid") is None:
        return "User has no entries in /etc/subuid and /etc/subgid"
    try:
        result = subprocess.run(["unshare", "--user", "--map-root-user", "true"], capture_output=True, timeout=10)
        if result.returncode != 0:
            return f"Unprivileged user namespaces are not available: {result.stderr.decode(errors='replace').strip()}"
    except Exception as e:
        return f"Unprivileged user namespaces are not available: {e}"
    return None

def _subordinate_range(path: str) -> tuple[int, int] | None:
    import pwd
    names = {str(os.getuid()), pwd.getpwuid(os.getuid()).pw_name}
    try:
        with open(path, encoding="utf-8") as file:
            for line in file:
                parts = line.strip().split(":")
                if len(parts) == 3 and parts[0] in names and int(parts[2]) > 0:
                    return int(parts[1]), min(int(parts[2]), MAPPED_IDS_COUNT)
    except (OSError, ValueError):
        pass
    return None

# ------------------------------------------------------------------------------
# Running scripts in namespace.

class NamespaceProcess:
    """Runs bash script as root of new user namespace with mapped id range, in own mount, pid, uts and ipc namespaces.
    Output lines are passed to output handler. Runs without controlling terminal (portage changes owner of its tty)."""

    def __init__(self, script: str, output_handler):
        self.script = script
        self.output_handler = output_handler
        self.process: subprocess.Popen | None = None
        self.cancelled = False

    def run(self) -> bool:
        self.start()
        for line in self.process.stdout:
            self.output_handler(line.rstrip("\n"))
        return self.process.wait() == 0 and not self.cancelled

    def start(self) -> subprocess.Popen:
        """Starts script, returns process with output in stdout. Run reads it and passes to handler."""
        uid_range, gid_range = _subordinate_range("/etc/subuid"), _subordinate_range("/etc/subgid")
        if uid_range is None or gid_range is None:
            raise RuntimeError("User has no entries in /etc/subuid and /etc/subgid")
        # Waits for id mapping (line on stdin) before starting script in nested namespaces.
        launcher = 'read _ && exec unshare --mount --pid --uts --ipc --fork --kill-child --propagation private bash -c "$0"'
        self.process = subprocess.Popen(
            ["unshare", "--user", "bash", "-c", launcher, self.script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True, text=True, errors="replace", bufsize=1
        )
        try:
            self._wait_for_user_namespace()
            for command, ids, (start, count) in (("newuidmap", os.getuid(), uid_range), ("newgidmap", os.getgid(), gid_range)):
                subprocess.run([command, str(self.process.pid), "0", str(ids), "1", "1", str(start), str(count)],
                               check=True, capture_output=True, text=True)
            self.process.stdin.write("go\n")
            self.process.stdin.close()
        except Exception as e:
            self.terminate()
            error = e.stderr.strip() if isinstance(e, subprocess.CalledProcessError) and e.stderr else e
            raise RuntimeError(f"Failed to map user ids: {error}") from e
        return self.process

    def _wait_for_user_namespace(self):
        own_namespace = os.readlink("/proc/self/ns/user")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Namespace process exited")
            try:
                if os.readlink(f"/proc/{self.process.pid}/ns/user") != own_namespace:
                    return
            except OSError:
                pass
            time.sleep(0.02)
        raise RuntimeError("Timed out waiting for user namespace")

    def terminate(self):
        """Stops script with all its processes (killing pid namespace init ends whole namespace)."""
        self.cancelled = True
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

def run_in_namespace(script: str, output_handler, process_holder: list | None = None) -> bool:
    """Runs script in namespace, process is appended to process_holder (to allow cancelling)."""
    process = NamespaceProcess(script=script, output_handler=output_handler)
    if process_holder is not None:
        process_holder.append(process)
    return process.run()

# ------------------------------------------------------------------------------
# Extracted toolsets and snapshots.
# Files keep their owners (mapped ids), so they are extracted and removed inside namespace.

def _stamp(path: str) -> str:
    stat = os.stat(path)
    return f"{stat.st_size}:{int(stat.st_mtime)}"

def extracted_squashfs(squashfs_path: str, kind: str, output_handler, process_holder: list | None = None,
                       check_path: str | None = None) -> str:
    """Folder with extracted squashfs file, extracted again when file changed. Kind is subfolder (toolsets,
    snapshots). Device nodes can't be created in namespace and are skipped."""
    name = os.path.basename(squashfs_path)
    parent = os.path.join(rootless_directory(), kind)
    path = os.path.join(parent, name + ".d")
    stamp_path = path + ".stamp"
    stamp = _stamp(squashfs_path)
    try:
        with open(stamp_path, encoding="utf-8") as file:
            if file.read().strip() == stamp and os.path.isdir(path):
                output_handler(f"Using extracted {name}")
                return path
    except OSError:
        pass
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(stamp_path):
        os.remove(stamp_path)
    temporary_path = os.path.join(parent, f".{name}.{uuid.uuid4().hex}")
    output_handler(f"Extracting {name}, this is done once for every version of the file...")
    script = f"""
set -u
rm -rf {shlex.quote(path)}
unsquashfs -n -d {shlex.quote(temporary_path)} {shlex.quote(squashfs_path)} 2>&1 | grep -v -e "create_inode: failed to create character device" -e "^\\[" -e "^$" | tail -5
if [ ! -e {shlex.quote(os.path.join(temporary_path, check_path or ''))} ]; then
    echo "Extraction failed"
    rm -rf {shlex.quote(temporary_path)}
    exit 1
fi
mv {shlex.quote(temporary_path)} {shlex.quote(path)}
"""
    if not run_in_namespace(script, output_handler, process_holder):
        raise RuntimeError(f"Failed to extract {name}")
    with open(stamp_path, "w", encoding="utf-8") as file:
        file.write(stamp)
    return path

def remove_extracted_squashfs(squashfs_path: str, kind: str):
    """Removes extracted copy of squashfs file (eg. when toolset or snapshot is deleted), in background."""
    path = os.path.join(rootless_directory(), kind, os.path.basename(squashfs_path) + ".d")
    if os.path.exists(path + ".stamp"):
        os.remove(path + ".stamp")
    if os.path.isdir(path) and rootless_unsupported_reason() is None:
        threading.Thread(target=remove_in_namespace, args=([path], print), daemon=True).start()

def remove_in_namespace(paths: list[str], output_handler, process_holder: list | None = None) -> bool:
    """Removes files that can be owned by mapped ids."""
    existing = [path for path in paths if os.path.lexists(path)]
    if not existing:
        return True
    return run_in_namespace("rm -rf -- " + " ".join(shlex.quote(path) for path in existing), output_handler, process_holder)

def sessions_directory() -> str:
    return os.path.join(rootless_directory(), "sessions")

def distfiles_directory() -> str:
    return os.path.join(rootless_directory(), "distfiles")

def remove_stale_sessions(output_handler, process_holder: list | None = None):
    """Sessions left by interrupted builds (only one build runs at a time). Sessions of toolset environments are kept."""
    directory = sessions_directory()
    if os.path.isdir(directory):
        sessions = [os.path.join(directory, name) for name in os.listdir(directory) if name.startswith("build-")]
        if sessions:
            output_handler(f"Removing {len(sessions)} unfinished build session(s)")
            remove_in_namespace(sessions, output_handler, process_holder)


def writable_squashfs_copy(squashfs_path: str, output_handler, process_holder: list | None = None) -> str:
    """New extracted copy of squashfs file, for changing it and packing again. Remove it with remove_in_namespace."""
    directory = os.path.join(rootless_directory(), "work")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{os.path.basename(squashfs_path)}.{uuid.uuid4().hex}")
    output_handler(f"Extracting {os.path.basename(squashfs_path)}...")
    script = f"""
unsquashfs -n -d {shlex.quote(path)} {shlex.quote(squashfs_path)} 2>&1 | grep -v -e "create_inode: failed to create character device" -e "^$" | tail -5
[ -e {shlex.quote(os.path.join(path, "usr"))} ] || {{ echo "Extraction failed"; rm -rf {shlex.quote(path)}; exit 1; }}
"""
    if not run_in_namespace(script, output_handler, process_holder):
        raise RuntimeError(f"Failed to extract {os.path.basename(squashfs_path)}")
    return path

def create_work_directory(prefix: str) -> str:
    """Work directory in rootless directory, prefix can contain subdirectories (eg. 'toolsets/name/setup_')."""
    *subdirectories, name_prefix = prefix.rstrip("/").split("/")
    parent = os.path.join(rootless_directory(), "work", *subdirectories)
    os.makedirs(parent, exist_ok=True)
    return tempfile.mkdtemp(prefix=name_prefix, dir=parent)

def squashfs_process(source_directory: str, output_file: str) -> subprocess.Popen:
    """Packs directory to squashfs inside namespace, so files keep their owners. Output is like mksquashfs -percentage."""
    q = shlex.quote
    return NamespaceProcess(script=f"exec mksquashfs {q(source_directory)} {q(output_file)} -quiet -percentage -noappend",
                            output_handler=print).start()

def extract_tarball(tarball: str, directory: str, output_handler, process_holder: list | None = None) -> bool:
    """Extracts stage tarball with owners, special files and xattrs. Device nodes can't be created in namespace and are
    skipped (toolsets bind /dev). Prints PROGRESS: <0-1> lines."""
    script = f"""exec python3 - {shlex.quote(tarball)} {shlex.quote(directory)} <<'PYTHON'
import os, sys, tarfile
tarball, destination = sys.argv[1], os.path.abspath(sys.argv[2])
def stage_filter(member, dest_path):
    if member.ischr() or member.isblk():
        return None
    for path in [member.name] + ([member.linkname] if member.islnk() else []):
        target = os.path.normpath(os.path.join(destination, path))
        if os.path.isabs(path) or os.path.commonpath([target, destination]) != destination:
            raise tarfile.OutsideDestinationError(member, target)
    return member
with tarfile.open(tarball) as tar:
    members = tar.getmembers()
    total = sum(member.size for member in members) or 1
    done = 0
    for member in members:
        tar.extract(member, path=destination, filter=stage_filter)
        done += member.size
        print(f"PROGRESS: {{done / total}}", flush=True)
PYTHON
"""
    return run_in_namespace(script, output_handler, process_holder)

# ------------------------------------------------------------------------------
# Authorization.

class RootlessAuthorization:
    """Used instead of AuthorizationKeeper of root helper for actions running without root."""
    name = "Rootless"
    def retain(self):
        pass
    def release(self):
        pass

def authorize_toolset_action(callback, name: str | None = None):
    """Like RootHelperClient.authorize_and_run, but without asking for root password when toolsets can run without
    root. Callback is called in background thread, with RootlessAuthorization instead of authorization keeper."""
    if rootless_toolset_unsupported_reason() is None:
        threading.Thread(target=callback, args=(RootlessAuthorization(),), daemon=True).start()
        return
    from .root_helper_client import RootHelperClient
    if name is None:
        RootHelperClient.shared().authorize_and_run(callback=callback)
    else:
        RootHelperClient.shared().authorize_and_run(name=name, callback=callback)
