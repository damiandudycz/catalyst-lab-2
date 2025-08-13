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
from collections.abc import Iterable
from .project_stage_arguments import (
    StageArguments, StageArgumentTargetDetails, StageArgumentOption,
    StageArgumentType, StageArgumentDetails
)
from .project_stage_compression_mode import StageCompressionMode
from .project_stage_argument_serialization import ProjectStageArgumentSerialization
from .project_stage_automatic_option import StageAutomaticOption

class ProjectStage(Serializable):

    def __init__(self, id: uuid.UUID | None = None, parent_id: uuid.UUID | None = None, name: str = None, target_name: str | None = None, releng_template_name: str | None = None, profile: PortageProfile | None = None, data: dict = {}):
        self.event_bus = EventBus[ProjectStageEvent]()
        # Required variables in case not set in json:
        self.id = id or uuid.uuid4()
        self.parent = parent_id
        self.target = target_name
        self.name = name
        self.releng_template = releng_template_name
        self.profile = profile
        # Load available values from data dictionary
        for key, value in data.items():
            setattr(self, key, ProjectStageArgumentSerialization.deserialize(value))

    # Quick access properties:

    # --------------------------------------------------------------------------

    def serialize(self) -> dict:
        attrs = self.__dict__.copy()
        attrs.pop('event_bus', None)
        new_attrs = attrs.copy()
        # Map types to serialized format
        for key, value in attrs.items():
            if value is None:
                new_attrs.pop(key, None)
            else:
                new_attrs[key] = ProjectStageArgumentSerialization.serialize(value)
        return new_attrs

    @classmethod
    def init_from(cls, data: dict) -> Self:
        return cls(data=data)

class ProjectStageEvent(Enum):
    NAME_CHANGED = auto()

# ------------------------------------------------------------------------------
# Catalyst stage arguments analysis:

def load_catalyst_stage_arguments(toolset: Toolset, target_name: str | None) -> StageArguments:
    """Loads the list of arguments used by catalyst targets"""

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
            values = load_releng_templates(releng_directory=project_directory.get_releng_directory(), stage_name=stage.target, architecture=project_directory.get_architecture())
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
        case StageArgumentDetails.interpreter:
            values = project_directory.get_toolset().metadata.get(ToolsetApplication.QEMU.package, {}).get('interpreters', [])
            return [StageArgumentOption(raw=value, display=value, subtitle=None, value=value, argument=arg_details.details) for value in values]
        case StageArgumentDetails.parent:
            values = load_stage_possible_seeds(stage=stage, project_directory=project_directory)
            return [StageArgumentOption(raw=value, display=value.name, subtitle=None, value=value.id, argument=arg_details.details) for value in values]
    return None

def load_catalyst_stage_arguments_options_for_boolean(arg_details: StageArgumentTargetDetails | None) -> list[StageArgumentOption] | None:
    """Creates Yes/No StageArgumentOptions for given argument."""
    return [
        StageArgumentOption(raw=True, display="Yes", subtitle=None, value=True, argument=arg_details.details),
        StageArgumentOption(raw=False, display="No", subtitle=None, value=False, argument=arg_details.details)
    ]

def load_catalyst_stage_automatic_arguments_options(stage: ProjectStage, arg_details: StageArgumentTargetDetails | None) -> list[StageArgumentOption] | None:
    """Creates additional inheriting options for given argument."""
    if not arg_details:
        return []
    opt_parent = StageArgumentOption(raw=StageAutomaticOption.INHERIT_FROM_PARENT, display="Inherit from parent", subtitle="Set to the value used by parent stage", value=StageAutomaticOption.INHERIT_FROM_PARENT, argument=arg_details.details)
    opt_releng = StageArgumentOption(raw=StageAutomaticOption.INHERIT_FROM_RELENG_TEMPLATE, display="Inherit from Releng template", subtitle="Set to the value used by releng template", value=StageAutomaticOption.INHERIT_FROM_RELENG_TEMPLATE, argument=arg_details.details)
    opt_auto   = StageArgumentOption(raw=StageAutomaticOption.GENERATE_AUTOMATICALLY, display="Automatic", subtitle="Automatically set to correct value", value=StageAutomaticOption.GENERATE_AUTOMATICALLY, argument=arg_details.details)
    if getattr(stage, StageArgumentDetails.parent.name, None) is None:
        opt_parent.unsupported = True
    if getattr(stage, StageArgumentDetails.releng_template.name, None) is None:
        opt_releng.unsupported = True
    match arg_details.details:
        case StageArgumentDetails.profile:
            return [option for option in [opt_parent, opt_releng] if option is not None]
        case StageArgumentDetails.interpreter:
            return [option for option in [opt_auto, opt_releng] if option is not None]
        case StageArgumentDetails.compression_mode:
            return [option for option in [opt_auto, opt_releng] if option is not None]
        case StageArgumentDetails.repos:
            return [option for option in [opt_parent, opt_releng] if option is not None]
        case StageArgumentDetails.keep_repos:
            return [option for option in [opt_releng] if option is not None]
        case _: return []

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

