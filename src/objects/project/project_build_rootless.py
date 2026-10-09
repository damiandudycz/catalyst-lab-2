from __future__ import annotations
import functools, os, shlex, shutil, signal, subprocess, time, uuid
from .project_build import builds_location

# ------------------------------------------------------------------------------
# Building without root privileges.
# Catalyst runs as root of unprivileged user namespace, where ids 1..65536 are mapped to subordinate ids of user
# (/etc/subuid, /etc/subgid), so files can have their real owners (portage, shadow...). Things that need real root
# are replaced: toolset and snapshot squashfs files are extracted once (loop mounts are not allowed), host /dev is
# bound instead of creating device nodes, and catalyst runs through wrapper adjusting its mounts (catalyst_wrapper).
# Files written to host folders by namespace root are owned by user.

MAPPED_IDS_COUNT = 65536

def rootless_directory() -> str:
    """Extracted toolsets, snapshots, distfiles and temporary build sessions."""
    return os.path.join(builds_location(), ".rootless")

@functools.cache
def rootless_build_unsupported_reason() -> str | None:
    """None when builds can run without root, otherwise reason why not. Checked once while app runs."""
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
        uid_range, gid_range = _subordinate_range("/etc/subuid"), _subordinate_range("/etc/subgid")
        if uid_range is None or gid_range is None:
            raise RuntimeError("User has no entries in /etc/subuid and /etc/subgid")
        # Waits for id mapping (line on stdin) before starting script in nested namespaces.
        launcher = 'read _ && exec unshare --mount --pid --uts --ipc --fork --propagation private bash -c "$0"'
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
        for line in self.process.stdout:
            self.output_handler(line.rstrip("\n"))
        return self.process.wait() == 0 and not self.cancelled

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
    """Sessions left by interrupted builds."""
    directory = sessions_directory()
    if os.path.isdir(directory):
        sessions = [os.path.join(directory, name) for name in os.listdir(directory)]
        if sessions:
            output_handler(f"Removing {len(sessions)} unfinished build session(s)")
            remove_in_namespace(sessions, output_handler, process_holder)

# ------------------------------------------------------------------------------
# Building stage.

# Runs catalyst with mounts adjusted for user namespace. Catalyst itself is not modified.
CATALYST_WRAPPER = '''import os, shutil, subprocess, sys
import libmount

_Context = libmount.Context

class _SquashfuseMount:
    def __init__(self, source, target):
        self.source, self.target = source, target
    def mount(self):
        subprocess.run(["squashfuse", "-o", "ro", self.source, self.target], check=True)

class _DevptsMount:
    """Own devpts instance instead of bind of host /dev/pts. Ptys of host devpts have tty group of host, which isn't
    mapped in namespace, so portage can't chown them to portage user. /dev/ptmx is pointed to the new instance."""
    def __init__(self, target):
        self.target = target
    def mount(self):
        _Context(source="devpts", target=self.target, fstype="devpts",
                 options="newinstance,ptmxmode=0666,mode=0620,gid=5").mount()
        ptmx = os.path.join(os.path.dirname(self.target.rstrip("/")), "ptmx")
        _Context(source=os.path.join(self.target, "ptmx"), target=ptmx, options="bind").mount()

def Context(*args, **kwargs):
    source, options = str(kwargs.get("source")), kwargs.get("options")
    if options == "bind" and source == "/dev/pts":
        return _DevptsMount(kwargs["target"])
    # Mounts inherited from host are locked in user namespace, so non-recursive bind of /dev (which has locked
    # submounts) fails. Recursive bind also brings real device nodes into chroot.
    if options == "bind" and source == "/dev":
        kwargs["options"] = "rbind"
    if kwargs.get("fstype") == "squashfs":
        if os.path.isdir(source):
            kwargs["fstype"], kwargs["options"] = "", "bind" # Extracted snapshot.
        elif shutil.which("squashfuse"):
            return _SquashfuseMount(source, kwargs["target"])
    return _Context(*args, **kwargs)

libmount.Context = Context

# Catalyst passes Path of folder repository to portage RepoConfig, which expects str.
import catalyst.support as support
_get_repo_name_from_dir = support.get_repo_name_from_dir
support.get_repo_name_from_dir = lambda repo_path: _get_repo_name_from_dir(str(repo_path))

# Device nodes can't be created in user namespace, tar would fail on /dev/console and /dev/null of seed. Catalyst binds
# /dev into chroot anyway.
from DeComp.compress import CompressMap
_create_infodict = CompressMap.create_infodict
_DEVICE_EXCLUDES = ["--exclude=./dev/console", "--exclude=./dev/null"]
def create_infodict(self, *args, **kwargs):
    options = kwargs.get("other_options")
    if isinstance(options, str):
        kwargs["other_options"] = " ".join([options] + _DEVICE_EXCLUDES)
    elif isinstance(options, (list, tuple)):
        kwargs["other_options"] = type(options)(list(options) + _DEVICE_EXCLUDES)
    return _create_infodict(self, *args, **kwargs)
CompressMap.create_infodict = create_infodict

from catalyst.main import main
main(sys.argv[1:])
'''

def stage_build_session_script(toolset_path: str, bindings: list[tuple[str, str]], command: str,
                               diagnostics_command: str | None = None) -> str:
    """Script running command in toolset root (overlay over extracted toolset, changes are discarded), with host
    folders bound at given paths (host path, path in toolset). Diagnostics command runs after failure."""
    session = os.path.join(sessions_directory(), uuid.uuid4().hex)
    q = shlex.quote
    binds = "\n".join(f"bind {q(host)} {q(target)}" for host, target in bindings)
    diagnostics = f'[ $status != 0 ] && chroot "$ROOT" /usr/bin/env -i HOME=/tmp TERM=dumb PATH=/usr/sbin:/usr/bin:/sbin:/bin {diagnostics_command} < /dev/null' if diagnostics_command else ""
    return f"""set -u
SESSION={q(session)}
ROOT="$SESSION/root"; UPPER="$SESSION/upper"; WORK="$SESSION/work"
cleanup() {{
    cd /
    umount -R -l "$ROOT" 2>/dev/null
    # Mounts are on merged root, upper and work contain only files of this session.
    rm -rf --one-file-system "$UPPER" "$WORK"
    rmdir "$ROOT" "$SESSION" 2>/dev/null
}}
trap cleanup EXIT
mkdir -p "$ROOT" "$UPPER" "$WORK" || exit 1
mount -t overlay overlay -o lowerdir={q(toolset_path)},upperdir="$UPPER",workdir="$WORK" "$ROOT" || exit 1
mount --rbind /dev "$ROOT/dev" || exit 1
mount -t proc proc "$ROOT/proc" || exit 1
mount -t tmpfs tmpfs "$ROOT/tmp" && mount -t tmpfs tmpfs "$ROOT/run" || exit 1
cp -L /etc/resolv.conf "$ROOT/etc/resolv.conf" 2>/dev/null
bind() {{
    mkdir -p "$ROOT$2" && mount --rbind "$1" "$ROOT$2" || {{ echo "Failed to bind $1"; exit 1; }}
}}
{binds}
chroot "$ROOT" /usr/bin/env -i HOME=/tmp TERM=dumb PATH=/usr/sbin:/usr/bin:/sbin:/bin {command} < /dev/null
status=$?
{diagnostics}
exit $status
"""
