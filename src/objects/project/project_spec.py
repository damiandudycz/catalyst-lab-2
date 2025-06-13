from .repository import Serializable
from .toolset import Toolset, ToolsetApplication, ToolsetEnv
from .root_helper_client import RootHelperClient
from dataclasses import dataclass
from typing import Self, FrozenSet
import os, subprocess, ast

@dataclass
class StageArguments:
    required: FrozenSet[str]
    valid: FrozenSet[str]

class ProjectSpec(Serializable):

    def serialize(self) -> dict:
        return {
            "name": self.name
        }

    @classmethod
    def init_from(cls, data: dict) -> Self:
        try:
            name = data["name"]
        except KeyError:
            raise ValueError(f"Failed to parse {data}")
        return cls(
            name=name,
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
        ['/app/bin/unsquashfs', '-l', toolset_file_path, catalyst_path_stage],
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
        ['/app/bin/unsquashfs', '-cat', toolset_file_path, catalyst_path_stage_found],
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
        ['/app/bin/unsquashfs', '-l', toolset_file_path, f"{catalyst_path_targets}/*.py"],
        text=True
    )
    except_files = ['__init__.py']
    target_files = "\n".join(
        os.path.splitext(os.path.basename(line))[0] for line in output.splitlines()
        if line.strip().endswith('.py') and not os.path.basename(line) in except_files
    )
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

