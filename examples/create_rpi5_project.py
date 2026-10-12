#!/usr/bin/env python3
"""Creates "Raspberry Pi 5" example project in CatalystLab, based on rpi5 example from catalyst-lab (bash version).
Uses CatalystLab code installed in the system, so project has the same structure as one created in the app.
Run while CatalystLab is closed (it would overwrite projects list with its own copy)."""
import sys, os, subprocess

PKGDATADIR = os.environ.get("CATALYSTLAB_PKGDATADIR", "/usr/local/share/catalystlab")
# First free name is used, existing projects are never modified.
PROJECT_NAMES = ["Raspberry Pi 5", "Raspberry Pi 5 Example"]

sys.path.insert(1, PKGDATADIR)
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gio
Gio.Resource.load(os.path.join(PKGDATADIR, 'catalystlab.gresource'))._register()

import catalystlab.repositories
from catalystlab.repository import Repository
from catalystlab.architecture import Architecture
from catalystlab.toolset import ToolsetApplication
from catalystlab.project_directory import ProjectDirectory, ProjectConfiguration
from catalystlab.project_manager import ProjectManager
from catalystlab.project_stage import ProjectStage, apply_default_stage_arguments
from catalystlab.project_stage_portage_confdir import StagePortageConfdirSource as Confdir, stage_overlay_path
from catalystlab.snapshot import PortageProfile

ARCH = Architecture.arm64

# ------------------------------------------------------------------------------
# Stages (in tree order, parents first):

COMMON_FLAGS = "-O2 -pipe -mcpu=cortex-a76+crc+crypto -mtune=cortex-a76 -flto -Werror=odr -Werror=lto-type-mismatch -Werror=strict-aliasing"
CPU_FLAGS = "CPU_FLAGS_ARM: edsp neon thumb vfp vfpv3 vfpv4 vfp-d32 aes sha1 sha2 crc32 asimddp v4 v5 v6 v7 v8 thumb2"

PACKAGES_BASE = [
    "app-admin/sudo", "app-admin/sysklogd", "app-eselect/eselect-repository", "app-portage/gentoolkit",
    "dev-vcs/git", "net-misc/networkmanager", "net-misc/ntp", "sys-block/zram-init", "sys-devel/distcc",
    "sys-kernel/linux-headers", "dev-embedded/raspberrypi-utils", "sys-firmware/raspberrypi-wifi-ucode",
    "sys-kernel/raspberrypi-image", "sys-power/cpupower",
]
RCADD_BASE = [
    "zram-init|boot", "cpupower|boot", "dbus|default", "NetworkManager|default", "bluetooth|default",
    "sysklogd|default", "ntpd|default", "ntp-client|default", "sshd|default",
]
USE_DESKTOP = ["networkmanager", "pulseaudio", "bluetooth"]

PI_PORTAGE = {
    "package.license": "sys-firmware/raspberrypi-wifi-ucode *\nsys-kernel/raspberrypi-image *\nsys-boot/raspberrypi-firmware *\n",
    "package.use/01input-devices": "*/* INPUT_DEVICES: evdev libinput\n",
    "package.use/wpa_supplicant": "net-wireless/wpa_supplicant dbus\n",
}
PI_ROOT_OVERLAY = {
    "boot/cmdline.txt": "console=serial0,115200 console=tty1 root=/dev/mmcblk0p2 rootfstype=ext4 elevator=deadline fsck.repair=yes rootwait\n",
    "boot/config.txt": "dtparam=audio=on\narm_64bit=1\ndtoverlay=vc4-kms-v3d-pi5\n",
    "etc/conf.d/zram-init": 'load_on_start="yes"\nunload_on_stop="yes"\nnum_devices="1"\n\ntype0="swap"\nflag0=\nsize0="2048"\nmaxs0=2\nalgo0=lz4\n',
}

GNOME_LTO_PATCH = """diff --git a/src/application.h b/src/application.h
index b5bacb2f..773927de 100644
--- a/src/application.h
+++ b/src/application.h
@@ -2,6 +2,7 @@
 #ifndef _GSM_APPLICATION_H_
 #define _GSM_APPLICATION_H_

+#include <config.h>
 #include <gtkmm.h>
 #include <glibtop/cpu.h>
 #include <handy.h>
"""

STAGES = [
    dict(
        name="stage1-openrc", target="stage1", parent=None, template="stage1-openrc-23.spec",
        values=dict(rel_type="23.0-lto", common_flags=COMMON_FLAGS, catalyst_use=["lto"]),
        confdir=[Confdir.RELENG, Confdir.STAGE_OVERLAY],
        portage={"package.use/00cpu-flags": f"*/* {CPU_FLAGS}\n", "package.use/02video-cards": "*/* VIDEO_CARDS: vc4 v3d fbdev\n"},
    ),
    dict(name="stage3-openrc", target="stage3", parent="stage1-openrc", template="stage3-openrc-23.spec"),
    dict(name="stage3-desktop-openrc", target="stage3", parent="stage1-openrc", template="stage3d-openrc-23.spec"),
    dict(
        name="stage4-openrc", target="stage4", parent="stage3-openrc", template=None,
        values=dict(
            stage4_use=USE_DESKTOP, stage4_packages=PACKAGES_BASE, stage4_rcadd=RCADD_BASE,
            stage4_empty=["/var/cache/distfiles"], stage4_rm=["/root/.bash_history"],
        ),
        confdir=[Confdir.RELENG, Confdir.PARENT_OVERLAY, Confdir.STAGE_OVERLAY],
        portage=PI_PORTAGE, root_overlay=PI_ROOT_OVERLAY,
    ),
    dict(
        name="stage4-desktop-openrc", target="stage4", parent="stage3-desktop-openrc", template=None,
        values=dict(
            stage4_use=USE_DESKTOP, stage4_packages=PACKAGES_BASE + ["x11-base/xorg-server", "gnome-base/gdm"],
            stage4_rcadd=RCADD_BASE + ["display-manager|default"],
            stage4_empty=["/var/cache/distfiles"], stage4_rm=["/root/.bash_history"],
        ),
        confdir=[Confdir.RELENG, Confdir.PARENT_OVERLAY, Confdir.STAGE_OVERLAY],
        portage=PI_PORTAGE,
        root_overlay={**PI_ROOT_OVERLAY, "etc/conf.d/display-manager": 'CHECKVT=7\nDISPLAYMANAGER="gdm"\n'},
    ),
    dict(
        name="stage4-desktop-gnome-openrc", target="stage4", parent="stage4-desktop-openrc", template=None,
        values=dict(
            profile=PortageProfile(path="default/linux/arm64/23.0/desktop/gnome", stability="stable", repo="gentoo"),
            version_stamp="desktop-gnome-openrc-@TIMESTAMP@", # Parent stamp would give the same output path.
            stage4_use=USE_DESKTOP, stage4_packages=["gnome-base/gnome"],
            stage4_empty=["/var/cache/distfiles"], stage4_rm=["/root/.bash_history"],
        ),
        confdir=[Confdir.RELENG, Confdir.PARENT_OVERLAY, Confdir.STAGE_OVERLAY],
        portage={"patches/gnome-extra/gnome-system-monitor/gnome-system-monitor-45_fix-lto-build.patch": GNOME_LTO_PATCH},
    ),
]

# ------------------------------------------------------------------------------

def fail(message: str):
    print(f"ERROR: {message}")
    sys.exit(1)

def write_files(base: str, files: dict[str, str]):
    for relative_path, content in files.items():
        path = os.path.join(base, relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            file.write(content)

def git(path: str, *args):
    subprocess.run(["git", "-C", path, *args], check=True, stdout=subprocess.DEVNULL)

def main():
    manager = ProjectManager.shared()
    project_name = next((name for name in PROJECT_NAMES if manager.is_name_available(name=name)), None)
    if project_name is None:
        fail(f"Projects {', '.join(PROJECT_NAMES)} already exist.")
    toolset = next((item for item in Repository.Toolset.value if item.get_app_install(ToolsetApplication.CATALYST)), None)
    releng = next(iter(Repository.RelengDirectory.value), None)
    snapshot = max(Repository.Snapshot.value, key=lambda item: item.date.timestamp() if item.date else 0, default=None)
    if toolset is None: fail("No toolset with Catalyst installed.")
    if releng is None: fail("No releng directory.")
    if snapshot is None: fail("No snapshot.")
    print(f"Toolset: {toolset.name} | Releng: {releng.name} | Snapshot: {snapshot.filename}")

    # Project (same as ProjectInstallation with new local directory):
    project = ProjectDirectory(name=project_name)
    path = project.directory_path()
    os.makedirs(os.path.join(path, "stages"))
    project.metadata = ProjectConfiguration(
        toolset_id=toolset.uuid, releng_directory_id=releng.id, snapshot_id=snapshot.filename, architecture=ARCH
    )
    git(path, "init", "--initial-branch=main")
    manager.add_directory(directory=project)
    project.stages = []
    print(f"Project created: {path}")

    # Stages (same as ProjectStageInstallation, plus example settings):
    specs_path = os.path.join(releng.directory_path(), "releases", "specs", ARCH.releng_base_arch().value)
    stages_by_name = {}
    for definition in STAGES:
        template = definition["template"]
        if template and not os.path.isfile(os.path.join(specs_path, template)):
            print(f"  Warning: releng template {template} not found, stage {definition['name']} created without it.")
            template = None
        parent = stages_by_name.get(definition["parent"])
        stage = ProjectStage(name=definition["name"], target_name=definition["target"], parent_id=parent.id if parent else None, releng_template_name=template)
        try:
            defaults = apply_default_stage_arguments(project_directory=project, stage=stage)
        except Exception as e:
            defaults = {}
            print(f"  Warning: failed to set default values for {stage.name}: {e}")
        for attribute, value in definition.get("values", {}).items():
            setattr(stage, attribute, value)
        if "confdir" in definition:
            stage.portage_confdir = definition["confdir"]
        root_overlay = definition.get("root_overlay")
        root_overlay_path = os.path.join(project.stage_directory_path(name=stage.name), "root_overlay")
        if root_overlay:
            stage.stage4_root_overlay = root_overlay_path
        project.install_stage(stage=stage) # Before writing files, it requires stage directory to not exist.
        project.add_stage(stage)
        write_files(stage_overlay_path(project, stage), definition.get("portage", {}))
        if root_overlay:
            write_files(root_overlay_path, root_overlay)
        stages_by_name[stage.name] = stage
        print(f"  Stage {stage.name}: {len(defaults)} defaults, {len(definition.get('values', {}))} custom values")

    try:
        git(path, "add", ".")
        git(path, "commit", "-m", "Raspberry Pi 5 example project")
    except subprocess.CalledProcessError:
        print("  Warning: git commit failed (git user not configured?), project files are left uncommitted.")
    print("Done. Start CatalystLab to see the project.")

main()
