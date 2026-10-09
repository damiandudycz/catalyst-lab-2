from __future__ import annotations
import os, shlex, uuid
from .rootless import sessions_directory

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
    session = os.path.join(sessions_directory(), f"build-{uuid.uuid4().hex}")
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
