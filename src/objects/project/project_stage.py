from __future__ import annotations
import os, subprocess, ast, uuid
from .repository import Serializable
from .toolset import Toolset, ToolsetApplication, ToolsetEnv
from .root_helper_client import RootHelperClient
from .releng_directory import RelengDirectory
from .event_bus import EventBus, SharedEvent
from .architecture import Architecture
from .snapshot import PortageProfile
from .snapshot import Snapshot
from .repository import Repository
from typing import Any
from dataclasses import dataclass
from typing import Self, FrozenSet
from enum import Enum, auto

class ProjectStage(Serializable):

    def __init__(self, id: uuid.UUID | None = None, parent_id: uuid.UUID | None = None, name: str = None, target_name: str | None = None, releng_template_name: str | None = None, profile: PortageProfile | None = None, data: dict = {}):
        self.data = data or {}
        self.event_bus = EventBus[ProjectStageEvent]()
        id = id or (uuid.UUID(data['id']) if data.get('id') else None) or uuid.uuid4()
        # Map some initil values to properties in data
        if id: self.data['id'] = str(id)
        if name: self.data['name'] = name
        if target_name: self.data['target'] = target_name
        if releng_template_name: self.data['releng_template'] = releng_template_name
        if profile: self.data['profile'] = profile.serialized()
        if parent_id: self.data['parent'] = str(parent_id)

    # Quick access properties:

    @property
    def id(self) -> uuid.UUID:
        return uuid.UUID(self.data['id'])

    @property
    def parent(self) -> uuid.UUID | None:
        return uuid.UUID(self.data['parent']) if self.data.get('parent') else None

    @property
    def name(self) -> str:
        return self.data['name']

    # --------------------------------------------------------------------------

    def serialize(self) -> dict:
        return self.data

    @classmethod
    def init_from(cls, data: dict) -> Self:
        return cls(data=data)

# ------------------------------------------------------------------------------
# Catalyst stage arguments analysis:

def load_catalyst_stage_arguments(toolset: Toolset, target_name: str | None) -> StageArguments:
    """Loads the list of arguments used by catalyst targets"""

    if toolset.get_app_install(ToolsetApplication.CATALYST) is None:
        raise RuntimeError("This toolset does not have Catalyst installed.")
    if toolset.env != ToolsetEnv.EXTERNAL:
        raise RuntimeError("Currently only EXTERNAL toolsets are supported for this functionality.")

    toolset_file_path = toolset.file_path()
    catalyst_path = "/usr/lib/python*/site-packages/catalyst"
    catalyst_path_base = os.path.join(catalyst_path, "base")
    catalyst_path_targets = os.path.join(catalyst_path, "targets")
    if target_name is not None:
        catalyst_path_stage = os.path.join(catalyst_path_targets, f"{target_name}.py")
    else:
        catalyst_path_stage = os.path.join(catalyst_path_base, "stagebase.py")

    # Find stage .py path:
    output = subprocess.check_output(
        ['unsquashfs', '-l', toolset_file_path, catalyst_path_stage],
        text=True
    )
    catalyst_path_stage_found = None
    catalyst_path_stage_basename = os.path.basename(catalyst_path_stage)
    for line in output.splitlines():
        line = line.strip()
        if line.endswith(catalyst_path_stage_basename):
            catalyst_path_stage_found = line[len('squashfs-root/'):]
            break
    if not catalyst_path_stage_found:
        raise FileNotFoundError("Could not find stage .py in the squashfs archive")

    # Read stage arguments:
    stage_content = subprocess.check_output(
        ['unsquashfs', '-cat', toolset_file_path, catalyst_path_stage_found],
        text=True
    )
    results = extract_frozenset_values(stage_content)
    required_values = results["required_values"]
    valid_values = list(set(results["required_values"]) | set(results["valid_values"]))

    # Append virtual values:
    valid_values.append(StageArgumentDetails.name.value)
    required_values.append(StageArgumentDetails.name.value)
    valid_values.append(StageArgumentDetails.parent.value)
    if target_name and target_name != 'stage1':
        required_values.append(StageArgumentDetails.parent.value)
    valid_values.append(StageArgumentDetails.releng_template.value)

    # Check if needs to also load stagebase and combine
    if target_name is not None:
        # Load the base stage arguments (stagebase.py)
        base_args = load_catalyst_stage_arguments(toolset, target_name=None)
        # Merge required and valid
        required = frozenset(required_values)
        valid = frozenset(valid_values)
        # Combine with base
        combined_required = required | base_args.required
        combined_valid = valid | base_args.valid
        return StageArguments(
            required=combined_required,
            valid=combined_valid
        )
    else:
        return StageArguments(
            required=frozenset(required_values),
            valid=frozenset(valid_values)
        )

def load_catalyst_stage_arguments_details(toolset: Toolset, target_name: str | None) -> dict[str, StageArgumentTargetDetails]:
    arguments_sets = load_catalyst_stage_arguments(toolset=toolset, target_name=target_name)
    arguments_details = {
        arg_name: StageArgumentTargetDetails(
            name=arg_name,
            required=(arg_name in arguments_sets.required),
            details=StageArgumentDetails.named(name=arg_name)
        )
        for arg_name in arguments_sets.valid
    }
    return dict(sorted(arguments_details.items()))

def load_catalyst_stage_arguments_options(project_directory, stage: ProjectStage, arg_details: StageArgumentTargetDetails | None) -> list[StageArgumentOption] | None:
    """Load possible list of options to select if argument allows it."""
    if not arg_details:
        return None
    match arg_details.details:
        case StageArgumentDetails.target:
            values = load_catalyst_targets(toolset=project_directory.get_toolset())
            return [StageArgumentOption(raw=value, display=value, subtitle=None, value=value, argument=arg_details.details) for value in values]
        case StageArgumentDetails.profile:
            values = project_directory.get_snapshot().load_profiles(arch=project_directory.get_architecture())
            return [StageArgumentOption(raw=value.path, display=value.path, subtitle=value.repo, value=value, argument=arg_details.details) for value in values]
        case StageArgumentDetails.releng_template:
            values = load_releng_templates(
                releng_directory=project_directory.get_releng_directory(),
                stage_name=StageArgumentDetails.target.get_from_stage(stage),
                architecture=project_directory.get_architecture()
            )
            return [StageArgumentOption(raw=value, display=value, subtitle=None, value=value, argument=arg_details.details) for value in values]
        case StageArgumentDetails.snapshot_treeish:
            values = Repository.Snapshot.value
            return [StageArgumentOption(raw=value, display=value.name, subtitle=value.short_details, value=value, argument=arg_details.details) for value in values]
        case StageArgumentDetails.compression_mode:
            values = StageCompressionMode
            return [StageArgumentOption(raw=value, display=value.name, subtitle=None, value=value, argument=arg_details.details) for value in values]
        case StageArgumentDetails.repos:
            values = Repository.OverlayDirectory.value
            return [StageArgumentOption(raw=value, display=value.name, subtitle=None, value=value.id, argument=arg_details.details) for value in values]
        case StageArgumentDetails.parent:
            values = load_stage_possible_seeds(stage=stage, project_directory=project_directory)
            return [StageArgumentOption(raw=value, display=value.name, subtitle=None, value=str(value.id), argument=arg_details.details) for value in values]
    return None

def load_catalyst_targets(toolset: Toolset) -> list[str]:
    """Loads the list of available targets as their paths inside squashfs file"""

    if toolset.get_app_install(ToolsetApplication.CATALYST) is None:
        raise RuntimeError("This toolset does not have Catalyst installed.")
    if toolset.env != ToolsetEnv.EXTERNAL:
        raise RuntimeError("Currently only EXTERNAL toolsets are supported for this functionality.")

    toolset_file_path = toolset.file_path()
    catalyst_path = "/usr/lib/python*/site-packages/catalyst"
    catalyst_path_targets = os.path.join(catalyst_path, "targets")

    # Read the list of potential target files:
    output = subprocess.check_output(
        ['unsquashfs', '-l', toolset_file_path, f"{catalyst_path_targets}/*.py"],
        text=True
    )
    except_files = {'__init__.py', 'snapshot.py'}
    target_files = [
        os.path.splitext(os.path.basename(line))[0]
        for line in output.splitlines()
        if line.strip().endswith('.py') and os.path.basename(line) not in except_files
    ]
    return target_files

# ------------------------------------------------------------------------------
# Loading releng templates:

def load_releng_templates(releng_directory: RelengDirectory, stage_name: str, architecture: Architecture) -> list[str]:
    specs_path = os.path.join(releng_directory.directory_path(), "releases/specs")
    templates = []
    prefix = architecture.releng_base_arch().value + "/"
    for root, _, files in os.walk(specs_path):
        for file in files:
            if file.endswith(".spec"):
                full_path = os.path.join(root, file)
                # Check if file contains the desired stage_name
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("target:") and line.split(":", 1)[1].strip() == stage_name.replace('_', '-'):
                                rel_path = os.path.relpath(full_path, specs_path)
                                if rel_path.startswith(prefix):
                                    rel_path = rel_path.removeprefix(prefix)
                                    templates.append(rel_path)
                                    break
                except Exception as e:
                    print(f"Warning: Failed to read {full_path}: {e}")
    return templates

def load_stage_possible_seeds(stage: ProjectStage, project_directory: ProjectDirectory):
    child_ids = _get_descendant_ids(stage=stage, project_directory=project_directory)
    available_stages = project_directory.stages[:]
    values = [
        s for s in available_stages
        if s.id not in child_ids and s.id != stage.id
    ]
    selected = next(
        (stage for stage in available_stages if stage.id == stage.parent),
        None
    )
    return values

def _get_descendant_ids(stage: ProjectStage, project_directory: ProjectDirectory) -> list[int]:
    project_stages_tree = project_directory.stages_tree()
    def find_node(nodes: list, target_id: int):
        for node in nodes:
            if node.value.id == target_id:
                return node
            result = find_node(node.children, target_id)
            if result:
                return result
        return None
    def collect_ids(node) -> list[int]:
        ids = []
        for child in node.children:
            ids.append(child.value.id)
            ids.extend(collect_ids(child))
        return ids
    root_node = find_node(project_stages_tree, stage.id)
    if not root_node:
        return []
    return collect_ids(root_node)


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

class StageArgumentType(Enum):
    raw = auto() # Raw text data
    raw_single_line = auto() # Raw with only one line of text
    select = auto() # Select one option from predefined list
    multiselect = auto() # Select multiple options from predefined list
    boolean = auto # yes / no

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
            case StageArgumentDetails.repos: return StageArgumentType.multiselect
            case _: return StageArgumentType.raw

    def get_from_stage(self, stage: ProjectStage):
        # Return deserialized value from given stage or None
        def map_class(cls) -> Any | None:
            return cls.init_from(stage.data[self.name]) if stage.data.get(self.name) else None
        def map_class_array(cls) -> list[Any] | None:
            return [cls.init_from(item) for item in stage.data[self.name]] if stage.data.get(self.name) else None
        def map_ids_array() -> list[uuid.UUID] | None:
            return [uuid.UUID(item) for item in stage.data[self.name]] if stage.data.get(self.name) else None
        match self:
            case StageArgumentDetails.profile: return map_class(cls=PortageProfile)
            case StageArgumentDetails.snapshot_treeish: return map_class(cls=Snapshot)
            case StageArgumentDetails.compression_mode: return map_class(cls=StageCompressionMode)
            case StageArgumentDetails.repos: return map_ids_array()
        # Options returned as raw values:
        return stage.data.get(self.name)

    def set_in_stage(self, stage: ProjectStage, value):
        # Serialize and store in given stage
        def serialize_class():
            stage.data[self.name] = value.serialize()
        def serialize_class_array():
            stage.data[self.name] = [item.serialize() for item in value]
        def serialize_ids_array():
            stage.data[self.name] = [str(item) for item in value]
        if value:
            match self:
                case StageArgumentDetails.profile: serialize_class()
                case StageArgumentDetails.snapshot_treeish: serialize_class()
                case StageArgumentDetails.compression_mode: serialize_class()
                case StageArgumentDetails.repos: return serialize_ids_array()
                # Save as raw value
                case _: stage.data[self.name] = value
        else:
            stage.data.pop(self.name, None)

class ProjectStageEvent(Enum):
    NAME_CHANGED = auto()

def _get_descendant_ids(project_directory: ProjectDirectory, stage: ProjectStage) -> list[int]:
    project_stages_tree = project_directory.stages_tree()
    def find_node(nodes: list, target_id: int):
        for node in nodes:
            if node.value.id == target_id:
                return node
            result = find_node(node.children, target_id)
            if result:
                return result
        return None
    def collect_ids(node) -> list[int]:
        ids = []
        for child in node.children:
            ids.append(child.value.id)
            ids.extend(collect_ids(child))
        return ids
    root_node = find_node(project_stages_tree, stage.id)
    if not root_node:
        return []
    return collect_ids(root_node)

def extract_frozenset_values(code_str: str) -> dict[str, list[str]]:
    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.values = {
                "required_values": [],
                "valid_values": [],
            }
            # Keep track of values assigned so far for substitution
            self._cache = {
                "required_values": [],
                "valid_values": [],
            }
        def visit_ClassDef(self, node: ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name):
                            attr_name = target.id
                            if attr_name in self.values:
                                # Extract new elements from right side
                                new_elements = self._extract_strings_from_expr(stmt.value)
                                # Update cache and values
                                self._cache[attr_name].extend(new_elements)
                                self.values[attr_name] = list(set(self._cache[attr_name]))  # unique
                elif isinstance(stmt, ast.AugAssign):
                    target = stmt.target
                    if isinstance(target, ast.Name):
                        attr_name = target.id
                        if attr_name in self.values:
                            new_elements = self._extract_strings_from_expr(stmt.value)
                            self._cache[attr_name].extend(new_elements)
                            self.values[attr_name] = list(set(self._cache[attr_name]))
            self.generic_visit(node)
        def visit_Assign(self, node):
            self._handle_assignment(node.targets[0], node.value)
        def visit_AugAssign(self, node):
            self._handle_assignment(node.target, node.value)
        def _handle_assignment(self, target, value):
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                attr_name = target.attr
                if attr_name in self.values:
                    new_elements = self._extract_strings_from_expr(value)
                    self.values[attr_name].extend(new_elements)
        def _extract_strings_from_expr(self, expr):
            elements = []
            if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "frozenset":
                if expr.args and isinstance(expr.args[0], (ast.List, ast.Set, ast.Tuple)):
                    for elt in expr.args[0].elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            elements.append(elt.value)
            elif isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
                elements.extend(self._extract_strings_from_expr(expr.left))
                elements.extend(self._extract_strings_from_expr(expr.right))
            elif isinstance(expr, ast.Name):
                # Substitute from cached values
                name = expr.id
                if name in self._cache:
                    elements.extend(self._cache[name])
            return elements
    tree = ast.parse(code_str)
    visitor = Visitor()
    visitor.visit(tree)
    return visitor.values

class StageCompressionMode(Enum):
    rsync = auto()
    lbzip2 = auto()
    bzip2 = auto()
    tar = auto()
    xz = auto()
    pixz = auto()
    gzip = auto()
    squashfs = auto()

    def serialize(self) -> str:
        return self.name

    @classmethod
    def init_from(cls, data: str) -> StageCompressionMode:
        return cls[data]

Serializable.register(StageCompressionMode)

