from __future__ import annotations
from typing import Self, FrozenSet
from dataclasses import dataclass
from enum import Enum, auto

@dataclass
class StageArguments:
    """Collects required and valid sets of arguments for given target."""
    required: FrozenSet[str]
    valid: FrozenSet[str]

@dataclass
class StageArgumentTargetDetails:
    """Gather details about argument of name for given target."""
    name: str
    required: bool
    details: StageArgumentDetails | None

    @property
    def display_name(self) -> str:
        return self.details.display_name if self.details else self.name

@dataclass
class StageArgumentOption:
    """Used to display options in lists and allowing to select them."""
    raw: str
    display: str
    subtitle: str | None
    value: Any
    argument: StageArgumentDetails
    unsupported: bool = False

class StageArgumentType(Enum):
    raw = auto() # Raw text data
    raw_single_line = auto() # Raw with only one line of text
    select = auto() # Select one option from predefined list
    multiselect = auto() # Select multiple options from predefined list
    boolean = auto() # yes / no

class StageArgumentDetails(Enum):
    version_stamp = "version_stamp"
    target = "target"
    subarch = "subarch"
    rel_type = "rel_type"
    profile = "profile"
    interpreter = "interpreter"
    chost = "chost"
    cbuild = "cbuild"
    cxxflags = "cxxflags"
    cflags = "cflags"
    fcflags = "fcflags"
    fflags = "fflags"
    asflags = "asflags"
    ldflags = "ldflags"
    common_flags = "common_flags"
    hostuse = "hostuse"
    repos = "repos"
    binrepo_path = "binrepo_path"
    catalyst_use = "catalyst_use"
    compression_mode = "compression_mode"
    decompressor_search_order = "decompressor_search_order"
    install_mask = "install_mask"
    keep_repos = "keep_repos"
    kerncache_path = "kerncache_path"
    pkgcache_path = "pkgcache_path"
    portage_confdir = "portage_confdir"
    portage_prefix = "portage_prefix"
    snapshot_treeish = "snapshot_treeish"
    source_subpath = "source_subpath"
    update_seed = "update_seed"
    update_seed_command = "update_seed_command"
    boot_kernel = 'boot/kernel'
    stage4_empty = 'stage4/empty'
    stage4_fsscript = 'stage4/fsscript'
    stage4_gk_mainargs = 'stage4/gk_mainargs'
    stage4_groups = 'stage4/groups'
    stage4_linuxrc = 'stage4/linuxrc'
    stage4_packages = 'stage4/packages'
    stage4_rcadd = 'stage4/rcadd'
    stage4_rcdel = 'stage4/rcdel'
    stage4_rm = 'stage4/rm'
    stage4_root_overlay = 'stage4/root_overlay'
    stage4_ssh_public_keys = 'stage4/ssh_public_keys'
    stage4_unmerge = 'stage4/unmerge'
    stage4_use = 'stage4/use'
    stage4_users = 'stage4/users'
    livecd_packages = 'livecd/packages'
    livecd_use = 'livecd/use'
    livecd_bootargs = 'livecd/bootargs'
    livecd_cdtar = 'livecd/cdtar'
    livecd_depclean = 'livecd/depclean'
    livecd_empty = 'livecd/empty'
    livecd_fsops = 'livecd/fsops'
    livecd_fsscript = 'livecd/fsscript'
    livecd_fstype = 'livecd/fstype'
    livecd_gk_mainargs = 'livecd/gk_mainargs'
    livecd_iso = 'livecd/iso'
    livecd_linuxrc = 'livecd/linuxrc'
    livecd_modblacklist = 'livecd/modblacklist'
    livecd_motd = 'livecd/motd'
    livecd_overlay = 'livecd/overlay'
    livecd_rcadd = 'livecd/rcadd'
    livecd_rcdel = 'livecd/rcdel'
    livecd_readme = 'livecd/readme'
    livecd_rm = 'livecd/rm'
    livecd_root_overlay = 'livecd/root_overlay'
    livecd_type = 'livecd/type'
    livecd_unmerge = 'livecd/unmerge'
    livecd_users = 'livecd/users'
    livecd_verify = 'livecd/verify'
    livecd_volid = 'livecd/volid'
    # Virtual properties:
    name = 'name'
    parent = 'parent'
    releng_template = 'releng_template'
    # ...

    @staticmethod
    def named(name: str) -> StageArgumentDetails | None:
        try:
            return StageArgumentDetails(name)
        except ValueError:
            return None

    @property
    def display_name(self) -> str:
        match self:
            case StageArgumentDetails.version_stamp: return "Version stamp"
            case StageArgumentDetails.profile: return "Profile"
            case StageArgumentDetails.repos: return "Repos"
            case StageArgumentDetails.target: return "Target"
            case StageArgumentDetails.asflags: return "ASFlags"
            case StageArgumentDetails.binrepo_path: return "Binrepo path"
            case StageArgumentDetails.catalyst_use: return "Catalyst USE"
            case StageArgumentDetails.cbuild: return "CBuild"
            case StageArgumentDetails.cflags: return "CFlags"
            case StageArgumentDetails.chost: return "CHost"
            case StageArgumentDetails.common_flags: return "Common flags"
            case StageArgumentDetails.compression_mode: return "Compression mode"
            case StageArgumentDetails.cxxflags: return "CXXFlags"
            case StageArgumentDetails.decompressor_search_order: return "Decompressor search order"
            case StageArgumentDetails.fcflags: return "FCFlags"
            case StageArgumentDetails.fflags: return "FFlags"
            case StageArgumentDetails.hostuse: return "Host USE"
            case StageArgumentDetails.install_mask: return "Install mask"
            case StageArgumentDetails.interpreter: return "Interpreter"
            case StageArgumentDetails.keep_repos: return "Keep repos"
            case StageArgumentDetails.kerncache_path: return "Kernel cache path"
            case StageArgumentDetails.ldflags: return "LDFlags"
            case StageArgumentDetails.pkgcache_path: return "PKG cache path"
            case StageArgumentDetails.portage_confdir: return "Portage confdir"
            case StageArgumentDetails.portage_prefix: return "Portage prefix"
            case StageArgumentDetails.rel_type: return "Rel type"
            case StageArgumentDetails.snapshot_treeish: return "Snapshot treeish"
            case StageArgumentDetails.source_subpath: return "Source subpath"
            case StageArgumentDetails.subarch: return "Subarch"
            case StageArgumentDetails.update_seed: return "Update seed"
            case StageArgumentDetails.update_seed_command: return "Update seed command"
            case StageArgumentDetails.boot_kernel: return "Boot / kernel"
            case StageArgumentDetails.stage4_empty: return "Empty"
            case StageArgumentDetails.stage4_fsscript: return "FS script"
            case StageArgumentDetails.stage4_gk_mainargs: return "GK mainargs"
            case StageArgumentDetails.stage4_groups: return "Groups"
            case StageArgumentDetails.stage4_linuxrc: return "LinuxRC"
            case StageArgumentDetails.stage4_packages: return "Packages"
            case StageArgumentDetails.stage4_rcadd: return "RCadd"
            case StageArgumentDetails.stage4_rcdel: return "RCdel"
            case StageArgumentDetails.stage4_rm: return "RM"
            case StageArgumentDetails.stage4_root_overlay: return "Root overlay"
            case StageArgumentDetails.stage4_ssh_public_keys: return "SSH public keys"
            case StageArgumentDetails.stage4_unmerge: return "Unmerge"
            case StageArgumentDetails.stage4_use: return "USE"
            case StageArgumentDetails.stage4_users: return "Users"
            case StageArgumentDetails.livecd_packages: return "Packages"
            case StageArgumentDetails.livecd_use: return "USE"
            case StageArgumentDetails.livecd_bootargs: return "BOOT args"
            case StageArgumentDetails.livecd_cdtar: return "CDTar"
            case StageArgumentDetails.livecd_depclean: return "Depclean"
            case StageArgumentDetails.livecd_empty: return "Empty"
            case StageArgumentDetails.livecd_fsops: return "FS ops"
            case StageArgumentDetails.livecd_fsscript: return "FS Script"
            case StageArgumentDetails.livecd_fstype: return "FS Type"
            case StageArgumentDetails.livecd_gk_mainargs: return "GK mainargs"
            case StageArgumentDetails.livecd_iso: return "ISO"
            case StageArgumentDetails.livecd_linuxrc: return "LinuxRC"
            case StageArgumentDetails.livecd_modblacklist: return "Modblacklist"
            case StageArgumentDetails.livecd_motd: return "Motd"
            case StageArgumentDetails.livecd_overlay: return "Overlay"
            case StageArgumentDetails.livecd_rcadd: return "RC add"
            case StageArgumentDetails.livecd_rcdel: return "RC del"
            case StageArgumentDetails.livecd_readme: return "Readme"
            case StageArgumentDetails.livecd_rm: return "RM"
            case StageArgumentDetails.livecd_root_overlay: return "Root overlay"
            case StageArgumentDetails.livecd_type: return "Type"
            case StageArgumentDetails.livecd_unmerge: return "Unmerge"
            case StageArgumentDetails.livecd_users: return "Users"
            case StageArgumentDetails.livecd_verify: return "Verify"
            case StageArgumentDetails.livecd_volid: return "Vol ID"
            # Virtual:
            case StageArgumentDetails.name: return "Name"
            case StageArgumentDetails.parent: return "Parent"
            case StageArgumentDetails.releng_template: return "Releng template"

    @property
    def type(self) -> StageArgumentType:
        match self:
            case StageArgumentDetails.profile: return StageArgumentType.select
            case StageArgumentDetails.target: return StageArgumentType.select
            case StageArgumentDetails.releng_template: return StageArgumentType.select
            case StageArgumentDetails.snapshot_treeish: return StageArgumentType.select
            case StageArgumentDetails.compression_mode: return StageArgumentType.select
            case StageArgumentDetails.parent: return StageArgumentType.select
            case StageArgumentDetails.subarch: return StageArgumentType.select
            case StageArgumentDetails.interpreter: return StageArgumentType.multiselect
            case StageArgumentDetails.repos: return StageArgumentType.multiselect
            case StageArgumentDetails.update_seed: return StageArgumentType.boolean
            case StageArgumentDetails.keep_repos: return StageArgumentType.boolean
            case _: return StageArgumentType.raw

