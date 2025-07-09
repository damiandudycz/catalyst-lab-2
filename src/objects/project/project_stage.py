from .repository import Serializable
from .toolset import Toolset, ToolsetApplication, ToolsetEnv
from .root_helper_client import RootHelperClient
from .releng_directory import RelengDirectory
from dataclasses import dataclass
from typing import Self, FrozenSet
import os, subprocess, ast, uuid
from enum import Enum, auto
from .event_bus import EventBus, SharedEvent
from .architecture import Architecture
from .snapshot import PortageProfile

@dataclass
class StageArguments:
    required: FrozenSet[str]
    valid: FrozenSet[str]

class ProjectStageEvent(Enum):
    NAME_CHANGED = auto()

class ProjectStage(Serializable):

    DOWNLOAD_SEED_ID = uuid.UUID("24245937-ef36-49c0-b467-1315ebe99fbe")

    def __init__(self, id: uuid.UUID | None, parent_id: uuid.UUID | None, name: str, target_name: str, releng_template_name: str | None, profile: PortageProfile | None):
        self.id = id or uuid.uuid4()
        self.parent_id = parent_id
        self.name = name
        self.target_name = target_name
        self.releng_template_name = releng_template_name
        self.profile = profile
        self.event_bus = EventBus[ProjectStageEvent]()

    def serialize(self) -> dict:
        return {
            "id": str(self.id),
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "name": self.name,
            "target_name": self.target_name,
            "releng_template_name": self.releng_template_name,
            "profile": self.profile.serialize() if self.profile else None
        }

    @property
    def short_details(self) -> str:
        return self.target_name

    @classmethod
    def init_from(cls, data: dict) -> Self:
        try:
            id = uuid.UUID(data["id"])
            parent_id = uuid.UUID(data["parent_id"]) if data.get("parent_id") else None
            name = data["name"]
            target_name = data["target_name"]
            releng_template_name = data.get('releng_template_name')
            profile = PortageProfile.init_from(data["profile"]) if data.get("profile") else None
        except KeyError:
            raise ValueError(f"Failed to parse {data}")
        return cls(
            id=id,
            parent_id = parent_id,
            name=name,
            target_name=target_name,
            releng_template_name=releng_template_name,
            profile=profile
        )

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

# ------------------------------------------------------------------------------
# Loading releng templates:

def load_releng_templates(releng_directory: RelengDirectory, stage_name: str, architecture: Architecture) -> list[str]:
    specs_path = os.path.join(releng_directory.directory_path(), "releases/specs")
    templates = []
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
                                if rel_path.startswith(architecture.releng_base_arch().value + "/"):
                                    templates.append(rel_path)
                                    break
                except Exception as e:
                    print(f"Warning: Failed to read {full_path}: {e}")
    return templates

