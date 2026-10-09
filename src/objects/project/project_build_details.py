from __future__ import annotations
import gzip, os, re, shlex, subprocess
from datetime import datetime

# ------------------------------------------------------------------------------
# Details of stage builds stored in build.json (StageBuild.details), for reproducing builds and reporting bugs:
# inputs (snapshot, toolset, catalyst, project commit, seed), environment (where build ran and its resources), failure
# (failed packages and reason, with logs collected for bug reports) and output (archive size, installed packages).

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# " * ERROR: net-libs/webkit-gtk-2.54.0-r411::gentoo failed (compile phase):"
_FAILED_PACKAGE = re.compile(r"\* ERROR: (\S+?)(?:::\S+)? failed \((\w+) phase\)")

# Known reasons of failures, by text in build output (first match is used).
_FAILURE_REASONS = [
    (re.compile(r"Killed signal terminated program|fatal error: Killed|out of memory|Cannot allocate memory|virtual memory exhausted", re.I),
     "out of memory, compiler was killed"),
    (re.compile(r"No space left on device"), "no space left on device"),
    (re.compile(r"Couldn't download|Fetch failed|Could not resolve host"), "download failed"),
    (re.compile(r"there are no ebuilds to satisfy|there are no ebuilds built with USE flags"), "package not found in repository"),
    (re.compile(r"The following USE changes are necessary"), "USE changes are needed"),
    (re.compile(r"The following keyword changes are necessary|masked by: "), "packages are masked"),
    (re.compile(r"Multiple package instances within a single package slot"), "slot conflict"),
    (re.compile(r"is blocking|Error: The above package list contains packages which cannot be"), "blocked packages"),
    (re.compile(r"internal compiler error"), "internal compiler error"),
]

def run_details(process) -> dict:
    """Inputs and environment shared by all stages of build run."""
    details = {"inputs": {}, "environment": {}}
    inputs, environment = details["inputs"], details["environment"]
    if snapshot := process.snapshot:
        inputs["snapshot"] = {"file": snapshot.filename, "date": snapshot.date.isoformat() if snapshot.date else None}
    if toolset := process.toolset:
        from .toolset_application import ToolsetApplication
        catalyst = toolset.get_app_install(ToolsetApplication.CATALYST)
        inputs["toolset"] = {"name": toolset.name, "id": str(toolset.uuid),
                             "catalyst": str(catalyst.version) if catalyst else None}
    if project := _project_commit(process.project_directory):
        inputs["project"] = project
    machine = process.machine
    if machine:
        environment.update({"runs_on": machine.name, "virtual_machine": True, "cpus": machine.cpus,
                            "memory_gib": machine.memory_gib, "swap_gib": getattr(machine, "swap_gib", 0)})
    else:
        environment.update({"runs_on": os.uname().nodename, "virtual_machine": False, "cpus": os.cpu_count(),
                            "memory_gib": _memory_gib()})
    environment["rootless"] = process.rootless
    return details

def seed_details(process, stage) -> dict | None:
    """Build of parent stage used as seed, or downloaded seed file."""
    parent = process.plan.parent(stage)
    if parent is None:
        subpath = process.seed_subpaths.get(stage.id)
        return {"file": os.path.basename(subpath)} if subpath else None
    build = process.stage_builds.get(parent.id) or process.plan.entries[parent.id].reused_build
    if build is None:
        return None
    return {"stage": build.stage_name, "build_id": str(build.id), "timestamp": build.timestamp, "artifact": build.artifact}

def failure_details(lines: list[str], failure_directory: str | None) -> dict:
    """Failed packages and reason found in build output, and logs collected for bug reports."""
    packages, reason = [], None
    for line in lines:
        line = _ANSI.sub("", line)
        if (match := _FAILED_PACKAGE.search(line)) and not any(item["package"] == match.group(1) for item in packages):
            packages.append({"package": match.group(1), "phase": match.group(2)})
        if reason is None:
            reason = next((text for pattern, text in _FAILURE_REASONS if pattern.search(line)), None)
    failure = {"packages": packages, "reason": reason}
    if failure_directory and os.path.isdir(failure_directory) and os.listdir(failure_directory):
        failure["logs"] = os.path.basename(failure_directory)
    return failure

def output_details(build) -> dict:
    """Archive size and packages installed in built stage (from catalyst CONTENTS), saved to packages file."""
    from .project_build import StageBuild
    output = {}
    if build.artifact_path and os.path.isfile(build.artifact_path):
        output["size"] = os.path.getsize(build.artifact_path)
    contents = f"{build.artifact_path}.CONTENTS.gz" if build.artifact_path else None
    if contents and os.path.isfile(contents):
        packages = installed_packages(contents)
        if packages:
            with open(os.path.join(build.path, StageBuild.PACKAGES_FILE), "w", encoding="utf-8") as file:
                file.write("\n".join(packages) + "\n")
            output["packages"] = len(packages)
    return output

# CONTENTS is tar listing, path is last field: "drwxr-xr-x root/root 0 2026-10-09 18:13 ./var/db/pkg/sys-apps/portage-3.0.82/".
_PACKAGE_DIRECTORY = re.compile(r"^\./var/db/pkg/([^/]+/[^/]+)/$")

def installed_packages(contents_path: str) -> list[str]:
    packages = set()
    try:
        with gzip.open(contents_path, "rt", encoding="utf-8", errors="replace") as file:
            for line in file:
                fields = line.split()
                if fields and (match := _PACKAGE_DIRECTORY.match(fields[-1])):
                    packages.add(match.group(1))
    except OSError as e:
        print(f"Failed to read {contents_path}: {e}")
    return sorted(packages)

def failure_logs_script(chroot: str, destination: str) -> str:
    """Bash collecting logs usually requested in Gentoo bug reports from chroot of failed build (ran after build
    failed, with repository and /proc mounted in chroot): emerge --info, and for every package left in
    /var/tmp/portage (failed ones, portage cleans finished): build.log, environment, emerge -pqv, and configure logs
    (config.log, CMake and meson logs)."""
    q = shlex.quote
    return f"""
FAILURE={q(destination)}
mkdir -p "$FAILURE"
echo "--- collecting logs for bug reports in $FAILURE"
chroot "{chroot}" /usr/bin/env -i HOME=/root TERM=dumb PATH=/usr/sbin:/usr/bin:/sbin:/bin emerge --info > "$FAILURE/emerge-info.txt" 2>&1
cp "{chroot}/etc/portage/make.conf" "$FAILURE/make.conf" 2>/dev/null
for PACKAGE_DIR in "{chroot}"/var/tmp/portage/*/*/; do
    [ -f "$PACKAGE_DIR/temp/build.log" ] || continue
    PACKAGE=${{PACKAGE_DIR#{chroot}/var/tmp/portage/}}; PACKAGE=${{PACKAGE%/}}
    DEST="$FAILURE/$(echo "$PACKAGE" | tr / _)"
    mkdir -p "$DEST"
    cp "$PACKAGE_DIR/temp/build.log" "$DEST/build.log"
    cp "$PACKAGE_DIR/temp/environment" "$DEST/environment" 2>/dev/null
    chroot "{chroot}" /usr/bin/env -i HOME=/root TERM=dumb PATH=/usr/sbin:/usr/bin:/sbin:/bin emerge -pqv "=$PACKAGE" > "$DEST/emerge-pqv.txt" 2>&1
    if [ -d "$PACKAGE_DIR/work" ]; then
        find "$PACKAGE_DIR/work" -maxdepth 6 -type f -size -20M \\( -name config.log -o -name CMakeError.log -o -name CMakeOutput.log \\
            -o -name CMakeConfigureLog.yaml -o -name meson-log.txt \\) 2>/dev/null | while read -r LOG; do
            RELATIVE=${{LOG#$PACKAGE_DIR/work/}}
            mkdir -p "$DEST/work-logs" && cp "$LOG" "$DEST/work-logs/$(echo "$RELATIVE" | tr / _)"
        done
    fi
    echo "Collected logs of $PACKAGE"
done
chmod -R a+rwX "$FAILURE" 2>/dev/null
"""

def _project_commit(project_directory) -> dict | None:
    """Commit of project Git directory, and whether it had uncommitted changes."""
    try:
        path = project_directory.directory_path()
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, timeout=10)
        if commit.returncode != 0:
            return None
        status = subprocess.run(["git", "status", "--porcelain"], cwd=path, capture_output=True, text=True, timeout=10)
        return {"commit": commit.stdout.strip(), "modified": bool(status.stdout.strip())}
    except (OSError, subprocess.SubprocessError):
        return None

def _memory_gib() -> int | None:
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3)
    except (ValueError, OSError):
        return None

def format_duration(duration) -> str:
    seconds = int(duration.total_seconds())
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {seconds:02d}s"
