"""Project templates: directories with template.toml describing stages, options and files of new project.

Local templates are directories in data/project_templates of the app, templates from Git repositories have the same
layout in root of repository. Repositories offered in the app are listed in data/project_templates/repositories.txt.
Format of templates is described in docs/project-templates.md.

Templates can't run code. Values in template.toml can contain {{ expressions }} and "when" conditions, evaluated by
small evaluator that only allows constants, template values, operators, conditional expressions and few functions
(see TemplateExpression), so templates from other repositories are safe to use.
"""
from __future__ import annotations
import ast, hashlib, os, re, shutil, subprocess, tempfile, tomllib, uuid
from dataclasses import dataclass, field
from typing import Any, Callable
from .architecture import Architecture

TEMPLATE_FILE = "template.toml"
TEMPLATE_FORMAT = 1
REPOSITORIES_FILE = "repositories.txt"
RENDERED_FILE_SUFFIX = ".template"

class TemplateError(Exception):
    pass

# ------------------------------------------------------------------------------
# Expressions.
# ------------------------------------------------------------------------------

_MAX_EXPRESSION_LENGTH = 2_000
_MAX_EVALUATION_STEPS = 10_000
_MAX_STRING_LENGTH = 100_000
_MAX_LIST_LENGTH = 10_000
_PLACEHOLDER_PATTERN = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)

def _string_argument(function_name: str, value) -> str:
    if not isinstance(value, str):
        raise TemplateError(f"{function_name}() expects text, got {type(value).__name__}")
    return value

# Functions available in expressions.
_FUNCTIONS: dict[str, Callable] = {
    "lower": lambda text: _string_argument("lower", text).lower(),
    "upper": lambda text: _string_argument("upper", text).upper(),
    "replace": lambda text, old, new: _string_argument("replace", text).replace(
        _string_argument("replace", old), _string_argument("replace", new)),
    "join": lambda separator, items: _string_argument("join", separator).join(
        _string_argument("join", item) for item in items),
}

class TemplateExpression:
    """Evaluates expression with whitelisted subset of Python syntax: constants, names of template values, lists,
    and/or/not, comparisons, in, +, -, conditional expressions (a if condition else b), f-strings and functions
    lower(text), upper(text), replace(text, old, new) and join(separator, items). Nothing else is allowed, so
    expressions can't access files, attributes of objects or run code."""

    def __init__(self, source: str):
        if not isinstance(source, str):
            raise TemplateError(f"Expression must be text: {source!r}")
        source = source.strip()
        if len(source) > _MAX_EXPRESSION_LENGTH:
            raise TemplateError(f"Expression is too long: {source[:50]}...")
        try:
            self.tree = ast.parse(source, mode="eval")
        except SyntaxError as e:
            raise TemplateError(f"Invalid expression '{source}': {e.msg}") from None
        self.source = source

    def evaluate(self, names: dict[str, Any]) -> Any:
        self._steps = 0
        self._names = names
        try:
            return self._evaluate(self.tree.body)
        except TemplateError as e:
            raise TemplateError(f"{e} (in '{self.source}')") from None
        except (TypeError, ValueError) as e:
            raise TemplateError(f"Failed to evaluate '{self.source}': {e}") from None

    def _checked(self, value):
        if isinstance(value, str) and len(value) > _MAX_STRING_LENGTH:
            raise TemplateError("Text value is too long")
        if isinstance(value, list) and len(value) > _MAX_LIST_LENGTH:
            raise TemplateError("List value is too long")
        return value

    def _evaluate(self, node: ast.AST):
        self._steps += 1
        if self._steps > _MAX_EVALUATION_STEPS:
            raise TemplateError("Expression is too complex")
        match node:
            case ast.Constant(value=value) if value is None or isinstance(value, (str, int, float, bool)):
                return value
            case ast.Name(id=name):
                if name not in self._names:
                    raise TemplateError(f"Unknown name '{name}'")
                return self._names[name]
            case ast.List(elts=elements) | ast.Tuple(elts=elements):
                return self._checked([self._evaluate(element) for element in elements])
            case ast.BoolOp(op=op, values=values):
                result = None
                for value in values:
                    result = self._evaluate(value)
                    if isinstance(op, ast.And) and not result:
                        return result
                    if isinstance(op, ast.Or) and result:
                        return result
                return result
            case ast.UnaryOp(op=ast.Not(), operand=operand):
                return not self._evaluate(operand)
            case ast.UnaryOp(op=ast.USub(), operand=operand):
                value = self._evaluate(operand)
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise TemplateError("Minus can be used only with numbers")
                return -value
            case ast.BinOp(left=left, op=ast.Add() | ast.Sub() as op, right=right):
                left_value, right_value = self._evaluate(left), self._evaluate(right)
                numbers = all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (left_value, right_value))
                if isinstance(op, ast.Sub):
                    if not numbers:
                        raise TemplateError("Minus can be used only with numbers")
                    return left_value - right_value
                if not numbers and not (type(left_value) is type(right_value) and isinstance(left_value, (str, list))):
                    raise TemplateError(f"Can't add {type(left_value).__name__} and {type(right_value).__name__}")
                return self._checked(left_value + right_value)
            case ast.Compare(left=left, ops=ops, comparators=comparators):
                left_value = self._evaluate(left)
                for op, comparator in zip(ops, comparators):
                    right_value = self._evaluate(comparator)
                    match op:
                        case ast.Eq(): result = left_value == right_value
                        case ast.NotEq(): result = left_value != right_value
                        case ast.Lt(): result = left_value < right_value
                        case ast.LtE(): result = left_value <= right_value
                        case ast.Gt(): result = left_value > right_value
                        case ast.GtE(): result = left_value >= right_value
                        case ast.In() | ast.NotIn():
                            if not isinstance(right_value, (str, list)):
                                raise TemplateError("'in' can be used only with text and lists")
                            result = (left_value in right_value) == isinstance(op, ast.In)
                        case _:
                            raise TemplateError(f"Comparison {type(op).__name__} is not allowed")
                    if not result:
                        return False
                    left_value = right_value
                return True
            case ast.IfExp(test=test, body=body, orelse=orelse):
                return self._evaluate(body) if self._evaluate(test) else self._evaluate(orelse)
            case ast.JoinedStr(values=values):
                return self._checked("".join(str(self._evaluate(value)) for value in values))
            case ast.FormattedValue(value=value, conversion=-1, format_spec=None):
                return self._evaluate(value)
            case ast.Call(func=ast.Name(id=name), args=args, keywords=[]) if name in _FUNCTIONS and not any(
                    isinstance(arg, ast.Starred) for arg in args):
                return self._checked(_FUNCTIONS[name](*[self._evaluate(arg) for arg in args]))
        raise TemplateError(f"{type(node).__name__} is not allowed in template expressions")

def evaluate_condition(condition: str | None, names: dict[str, Any]) -> bool:
    """Value of "when" condition, conditions that are not set are true."""
    return True if condition is None else bool(TemplateExpression(condition).evaluate(names))

def render_value(value, names: dict[str, Any]):
    """Replaces {{ expressions }} in text, also in lists and tables. Text that is only one expression gets value of
    that expression (can be list, boolean...), otherwise values are inserted as text."""
    if isinstance(value, str):
        placeholders = list(_PLACEHOLDER_PATTERN.finditer(value))
        if len(placeholders) == 1 and placeholders[0].span() == (0, len(value)):
            return TemplateExpression(placeholders[0].group(1)).evaluate(names)
        def replace(match: re.Match) -> str:
            result = TemplateExpression(match.group(1)).evaluate(names)
            return "" if result is None else str(result)
        rendered = _PLACEHOLDER_PATTERN.sub(replace, value)
        if len(rendered) > _MAX_STRING_LENGTH:
            raise TemplateError("Text value is too long")
        return rendered
    if isinstance(value, list):
        return [render_value(item, names) for item in value]
    if isinstance(value, dict):
        return {key: render_value(item, names) for key, item in value.items()}
    return value

# ------------------------------------------------------------------------------
# Template definition.
# ------------------------------------------------------------------------------

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_NAMES = {"project_name", "groups", "true", "false", "none"} | set(_FUNCTIONS)
_GROUP_STAGES_NAME = "_group_stages" # Stages selected for enabled groups, used when generating.
_RESERVED_STAGE_ARGUMENTS = {"id", "parent", "target", "name", "releng_template", "event_bus"}

class TemplateVariableType:
    CHOICE = "choice"
    BOOLEAN = "boolean"
    ARCHITECTURE = "architecture" # Choice of architecture names, selected value is also architecture of project.
    MULTIPLE = "multiple" # Any number of options, value is list of selected values (in order of options).
    ALL = (CHOICE, BOOLEAN, ARCHITECTURE, MULTIPLE)

@dataclass
class TemplateOption:
    value: Any
    title: str
    when: str | None = None

@dataclass
class TemplateVariable:
    id: str
    title: str
    description: str | None
    type: str
    options: list[TemplateOption]
    default: Any
    when: str | None

    def available_options(self, names: dict[str, Any]) -> list[TemplateOption]:
        return [option for option in self.options if evaluate_condition(option.when, names)]

@dataclass
class TemplateStage:
    id: str
    name: str
    target: str
    parent: str | None
    releng_template: str | None
    arguments: dict[str, Any]
    when: str | None

@dataclass
class TemplateFiles:
    source: str
    destination: str
    when: str | None

@dataclass
class TemplateGroup:
    """Configuration that can be enabled when creating project and applied to selected stages: arguments added to
    stages (lists are extended) and files copied to their directories."""
    id: str
    title: str
    description: str | None
    default: bool
    when: str | None
    stages: list[str] # Ids of stages group can be applied to.
    default_stages: list[str]
    arguments: dict[str, Any]
    files: list[TemplateFiles] # Destination is relative to stage directory.

@dataclass
class ProjectTemplateSelection:
    """Template and values of its variables selected for new project (data of TEMPLATE Git directory source)."""
    template: ProjectTemplate
    selected: dict[str, Any]

    @property
    def repository_url(self) -> str | None:
        return self.template.repository_url

@dataclass
class GeneratedStage:
    """Stage of project generated from template for selected options."""
    template_id: str
    name: str
    target: str
    parent: str | None # Template id of parent stage.
    releng_template: str | None
    arguments: dict[str, Any]
    groups: list[str] = field(default_factory=list) # Titles of groups applied to stage.

@dataclass
class GeneratedProject:
    stages: list[GeneratedStage]
    files: list[tuple[str, str]] # Absolute source path in template, destination path relative to project.
    architecture: Architecture | None

def _table(data: dict, key: str, context: str) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise TemplateError(f"{context}: {key} must be a table")
    return value

def _text(data: dict, key: str, context: str, required: bool = True) -> str | None:
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise TemplateError(f"{context}: {key} must be a text")
    return value

def _condition(data: dict, context: str) -> str | None:
    condition = _text(data, "when", context, required=False)
    if condition is not None:
        TemplateExpression(condition) # Syntax is checked when loading.
    return condition

def _parse_files(entries, context: str) -> list[TemplateFiles]:
    if not isinstance(entries, list):
        raise TemplateError(f"{context} must be a list of tables")
    files = []
    for index, files_data in enumerate(entries):
        entry_context = f"{context} {index + 1}"
        if not isinstance(files_data, dict):
            raise TemplateError(f"{entry_context} must be a table")
        files.append(TemplateFiles(
            source=_text(files_data, "source", entry_context), destination=_text(files_data, "destination", entry_context),
            when=_condition(files_data, entry_context)
        ))
    return files

@dataclass
class ProjectTemplate:
    path: str # Directory with template.toml.
    name: str
    description: str
    variables: list[TemplateVariable]
    values: dict[str, Any]
    stages: list[TemplateStage]
    files: list[TemplateFiles]
    groups: list[TemplateGroup] = field(default_factory=list)
    groups_title: str = "Additional configuration"
    repository_url: str | None = None # Git repository of template, None for templates included in app.
    # False for definition downloaded without other files of repository (to show options), files are checked when
    # template is applied from cloned repository.
    files_available: bool = True

    @classmethod
    def load(cls, path: str, repository_url: str | None = None, files_available: bool = True) -> ProjectTemplate:
        file_path = os.path.join(path, TEMPLATE_FILE)
        if not os.path.isfile(file_path):
            raise TemplateError(f"{TEMPLATE_FILE} not found")
        if os.path.getsize(file_path) > 1_000_000:
            raise TemplateError(f"{TEMPLATE_FILE} is too large")
        try:
            with open(file_path, "rb") as file:
                data = tomllib.load(file)
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            raise TemplateError(f"Invalid {TEMPLATE_FILE}: {e}") from None
        if data.get("format", TEMPLATE_FORMAT) != TEMPLATE_FORMAT:
            raise TemplateError(f"Template format {data.get('format')} is not supported, update Catalyst Lab")
        name = _text(data, "name", "Template")
        description = _text(data, "description", "Template", required=False) or ""
        # Variables:
        variables = []
        ids = set()
        for index, variable_data in enumerate(data.get("variables", [])):
            context = f"Variable {index + 1}"
            if not isinstance(variable_data, dict):
                raise TemplateError(f"{context} must be a table")
            variable_id = _text(variable_data, "id", context)
            context = f"Variable {variable_id}"
            if not _IDENTIFIER_PATTERN.match(variable_id) or variable_id.lower() in _RESERVED_NAMES or variable_id in ids:
                raise TemplateError(f"{context}: invalid or duplicated id")
            ids.add(variable_id)
            variable_type = variable_data.get("type", TemplateVariableType.CHOICE)
            if variable_type not in TemplateVariableType.ALL:
                raise TemplateError(f"{context}: unknown type {variable_type}")
            options = []
            if variable_type == TemplateVariableType.BOOLEAN:
                options = [TemplateOption(value=True, title="Yes"), TemplateOption(value=False, title="No")]
            else:
                for option_data in variable_data.get("options", []):
                    if isinstance(option_data, str):
                        option_data = {"value": option_data}
                    if not isinstance(option_data, dict):
                        raise TemplateError(f"{context}: options must be texts or tables")
                    value = _text(option_data, "value", context)
                    if variable_type == TemplateVariableType.ARCHITECTURE and value not in Architecture.__members__:
                        raise TemplateError(f"{context}: unknown architecture {value}")
                    options.append(TemplateOption(
                        value=value, title=_text(option_data, "title", context, required=False) or value,
                        when=_condition(option_data, context)
                    ))
                if not options:
                    raise TemplateError(f"{context}: no options")
            default = variable_data.get("default")
            option_values = [option.value for option in options]
            if variable_type == TemplateVariableType.MULTIPLE:
                default = [] if default is None else default
                if not isinstance(default, list) or any(value not in option_values for value in default):
                    raise TemplateError(f"{context}: default value must be list of options")
            elif default is not None and default not in option_values:
                raise TemplateError(f"{context}: default value {default} is not one of options")
            variables.append(TemplateVariable(
                id=variable_id, title=_text(variable_data, "title", context, required=False) or variable_id,
                description=_text(variable_data, "description", context, required=False),
                type=variable_type, options=options, default=default, when=_condition(variable_data, context)
            ))
        if sum(variable.type == TemplateVariableType.ARCHITECTURE for variable in variables) > 1:
            raise TemplateError("Template can have only one architecture variable")
        # Computed values:
        values = _table(data, "values", "Template")
        for key in values:
            if not _IDENTIFIER_PATTERN.match(key) or key.lower() in _RESERVED_NAMES or key in ids:
                raise TemplateError(f"Value {key}: invalid or duplicated name")
            ids.add(key)
        # Stages:
        stages = []
        stage_ids = set()
        for index, stage_data in enumerate(data.get("stages", [])):
            context = f"Stage {index + 1}"
            if not isinstance(stage_data, dict):
                raise TemplateError(f"{context} must be a table")
            stage_id = _text(stage_data, "id", context)
            context = f"Stage {stage_id}"
            if stage_id in stage_ids:
                raise TemplateError(f"{context}: duplicated id")
            parent = _text(stage_data, "parent", context, required=False)
            if parent is not None and parent not in stage_ids:
                raise TemplateError(f"{context}: parent {parent} must be defined before this stage")
            stage_ids.add(stage_id)
            arguments = _table(stage_data, "arguments", context)
            for key in arguments:
                if not _IDENTIFIER_PATTERN.match(key) or key in _RESERVED_STAGE_ARGUMENTS:
                    raise TemplateError(f"{context}: argument {key} can't be set in arguments")
            stages.append(TemplateStage(
                id=stage_id, name=_text(stage_data, "name", context), target=_text(stage_data, "target", context),
                parent=parent, releng_template=_text(stage_data, "releng_template", context, required=False),
                arguments=arguments, when=_condition(stage_data, context)
            ))
        files = _parse_files(data.get("files", []), "Files")
        # Groups:
        groups = []
        group_ids = set()
        for index, group_data in enumerate(data.get("groups", [])):
            context = f"Group {index + 1}"
            if not isinstance(group_data, dict):
                raise TemplateError(f"{context} must be a table")
            group_id = _text(group_data, "id", context)
            context = f"Group {group_id}"
            if not _IDENTIFIER_PATTERN.match(group_id) or group_id in group_ids:
                raise TemplateError(f"{context}: invalid or duplicated id")
            group_ids.add(group_id)
            group_stages = group_data.get("stages")
            if not isinstance(group_stages, list) or not group_stages or any(stage_id not in stage_ids for stage_id in group_stages):
                raise TemplateError(f"{context}: stages must be list of ids of stages")
            default_stages = group_data.get("default_stages", group_stages)
            if not isinstance(default_stages, list) or any(stage_id not in group_stages for stage_id in default_stages):
                raise TemplateError(f"{context}: default_stages must be list of its stages")
            default = group_data.get("default", False)
            if not isinstance(default, bool):
                raise TemplateError(f"{context}: default must be true or false")
            arguments = _table(group_data, "arguments", context)
            for key in arguments:
                if not _IDENTIFIER_PATTERN.match(key) or key in _RESERVED_STAGE_ARGUMENTS:
                    raise TemplateError(f"{context}: argument {key} can't be set in arguments")
            groups.append(TemplateGroup(
                id=group_id, title=_text(group_data, "title", context, required=False) or group_id,
                description=_text(group_data, "description", context, required=False), default=default,
                when=_condition(group_data, context), stages=group_stages, default_stages=default_stages,
                arguments=arguments, files=_parse_files(group_data.get("files", []), f"{context} files")
            ))
        groups_title = _text(data, "groups_title", "Template", required=False) or "Additional configuration"
        return cls(path=path, name=name, description=description, variables=variables, values=values, stages=stages,
                   files=files, groups=groups, groups_title=groups_title, repository_url=repository_url,
                   files_available=files_available)

    @property
    def architecture_variable(self) -> TemplateVariable | None:
        return next((variable for variable in self.variables if variable.type == TemplateVariableType.ARCHITECTURE), None)

    def resolve(self, selected: dict[str, Any], project_name: str = "") -> dict[str, Any]:
        """Names available in expressions for selected values of variables. Variables that are not used get None,
        selections that are not available fall back to default or first available option."""
        names: dict[str, Any] = {"project_name": project_name}
        for variable in self.variables:
            if not evaluate_condition(variable.when, names):
                names[variable.id] = None
                continue
            options = [option.value for option in variable.available_options(names)]
            if variable.type == TemplateVariableType.MULTIPLE:
                # Selection of user, or default, without options that are not available.
                value = selected.get(variable.id)
                value = variable.default if not isinstance(value, list) else value
                names[variable.id] = [option for option in options if option in value]
                continue
            if not options:
                raise TemplateError(f"Variable {variable.id} has no available options")
            value = selected.get(variable.id)
            if value not in options:
                value = variable.default if variable.default in options else options[0]
            names[variable.id] = value
        # Groups: selected as {"enabled": bool, "stages": [stage ids]} under "group:<id>", defaults otherwise.
        enabled_groups, group_stages = [], {}
        for group in self.groups:
            if not evaluate_condition(group.when, names):
                continue
            selection = selected.get(f"group:{group.id}")
            selection = selection if isinstance(selection, dict) else {}
            if not selection.get("enabled", group.default):
                continue
            stages = selection.get("stages")
            stages = group.default_stages if not isinstance(stages, list) else stages
            enabled_groups.append(group.id)
            group_stages[group.id] = [stage_id for stage_id in group.stages if stage_id in stages]
        names["groups"] = enabled_groups
        names[_GROUP_STAGES_NAME] = group_stages
        for key, expression in self.values.items():
            names[key] = render_value(expression, names)
        return names

    def _files_paths(self, entry: TemplateFiles, names: dict[str, Any], base: str) -> tuple[str, str]:
        return _template_files_paths(self, entry, names, base)

    def available_groups(self, names: dict[str, Any]) -> list[TemplateGroup]:
        """Groups that can be enabled for selected values of variables."""
        return [group for group in self.groups if evaluate_condition(group.when, names)]

    def visible_variables(self, names: dict[str, Any]) -> list[TemplateVariable]:
        """Variables used for current selection (with "when" condition that is true)."""
        visible = []
        partial: dict[str, Any] = {"project_name": names.get("project_name", "")}
        for variable in self.variables:
            if evaluate_condition(variable.when, partial):
                visible.append(variable)
            partial[variable.id] = names.get(variable.id)
        return visible

    def generate(self, names: dict[str, Any]) -> GeneratedProject:
        """Stages and files of project for values resolved with resolve()."""
        stages: list[GeneratedStage] = []
        generated_ids = set()
        stage_names = set()
        for stage in self.stages:
            if not evaluate_condition(stage.when, names):
                continue
            context = f"Stage {stage.id}"
            if stage.parent is not None and stage.parent not in generated_ids:
                raise TemplateError(f"{context}: parent {stage.parent} is not created for selected options")
            name = render_value(stage.name, names)
            if not isinstance(name, str) or not name.strip() or "/" in name or name.startswith(".") or "\0" in name:
                raise TemplateError(f"{context}: invalid name {name!r}")
            if name in stage_names:
                raise TemplateError(f"{context}: name {name} is used by other stage")
            stage_names.add(name)
            target = render_value(stage.target, names)
            releng_template = render_value(stage.releng_template, names) or None
            if not isinstance(target, str) or (releng_template is not None and not isinstance(releng_template, str)):
                raise TemplateError(f"{context}: target and releng template must be texts")
            if releng_template and (os.path.isabs(releng_template) or ".." in releng_template.split("/")):
                raise TemplateError(f"{context}: releng template must be path inside releng specs")
            arguments = {}
            for key, value in stage.arguments.items():
                value = render_value(value, names)
                if value is None or value == "" or value == []:
                    continue
                arguments[key] = _stage_argument_value(value, f"{context}, argument {key}")
            applied_groups = []
            for group in self.groups:
                if stage.id not in names.get(_GROUP_STAGES_NAME, {}).get(group.id, []):
                    continue
                applied_groups.append(group.title)
                for key, value in group.arguments.items():
                    value = render_value(value, names)
                    if value is None or value == "" or value == []:
                        continue
                    value = _stage_argument_value(value, f"Group {group.id}, argument {key}")
                    existing = arguments.get(key)
                    if isinstance(existing, list) and isinstance(value, list):
                        arguments[key] = existing + [item for item in value if item not in existing]
                    else:
                        arguments[key] = value
            stages.append(GeneratedStage(template_id=stage.id, name=name, target=target, parent=stage.parent,
                                         releng_template=releng_template, arguments=arguments, groups=applied_groups))
            generated_ids.add(stage.id)
        files = []
        for entry in self.files:
            if evaluate_condition(entry.when, names):
                files.append(self._files_paths(entry, names, base=""))
        # Files of groups, in directories of stages group is applied to.
        stage_names = {stage.template_id: stage.name for stage in stages}
        for group in self.groups:
            for stage_id in names.get(_GROUP_STAGES_NAME, {}).get(group.id, []):
                if stage_id not in stage_names:
                    continue
                for entry in group.files:
                    if evaluate_condition(entry.when, names):
                        files.append(self._files_paths(entry, names, base=os.path.join("stages", stage_names[stage_id])))
        architecture_variable = self.architecture_variable
        architecture_name = names.get(architecture_variable.id) if architecture_variable else None
        return GeneratedProject(stages=stages, files=files,
                                architecture=Architecture[architecture_name] if architecture_name else None)

def _template_files_paths(template: ProjectTemplate, entry: TemplateFiles, names: dict[str, Any], base: str) -> tuple[str, str]:
    """Source path in template and destination in project (relative to base directory in project) of files."""
    template_root = os.path.realpath(template.path)
    source = os.path.realpath(os.path.join(template_root, str(render_value(entry.source, names))))
    destination = os.path.normpath(str(render_value(entry.destination, names)))
    if not source.startswith(template_root + os.sep):
        raise TemplateError(f"Files source {entry.source} is outside of template")
    if os.path.isabs(destination) or destination == ".." or destination.startswith(".." + os.sep):
        raise TemplateError(f"Files destination {entry.destination} is outside of {'stage' if base else 'project'}")
    destination = os.path.normpath(os.path.join(base, destination))
    if destination.split(os.sep)[0] == ".git":
        raise TemplateError(f"Files destination {entry.destination} is outside of project")
    if template.files_available and not os.path.exists(source):
        raise TemplateError(f"Files source {entry.source} doesn't exist")
    return source, destination

def _stage_argument_value(value, context: str):
    """Value of stage argument from template: texts, numbers, booleans and their lists are used directly, tables
    with type and value use format of stage.json."""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        # Items that are none (also tables with none value) are skipped, so expressions can leave them out.
        return [_stage_argument_value(item, context) for item in value
                if item is not None and not (isinstance(item, dict) and item.get("value") is None)]
    if isinstance(value, dict) and set(value) == {"type", "value"}:
        from .project_stage_argument_serialization import ProjectStageArgumentSerialization
        try:
            return ProjectStageArgumentSerialization.deserialize(value)
        except Exception as e:
            raise TemplateError(f"{context}: invalid value {value}: {e}") from None
    raise TemplateError(f"{context}: unsupported value {value!r}")

# ------------------------------------------------------------------------------
# Available templates.
# ------------------------------------------------------------------------------

def templates_directory() -> str | None:
    """data/project_templates installed with app (share/catalystlab/project_templates)."""
    import sys
    from gi.repository import GLib
    candidates = [os.path.join(directory, "catalystlab", "project_templates") for directory in GLib.get_system_data_dirs()]
    # Installed modules are in share/catalystlab/catalystlab, macOS bundle has data in share/catalystlab.
    candidates.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "project_templates"))
    if hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, "share", "catalystlab", "project_templates"))
    return next((path for path in candidates if os.path.isdir(path)), None)

def local_templates() -> list[ProjectTemplate | tuple[str, TemplateError]]:
    """Templates included in app, sorted by name. Templates that fail to load are returned as (path, error)."""
    directory = templates_directory()
    if directory is None:
        return []
    templates = []
    for entry in sorted(os.listdir(directory)):
        path = os.path.join(directory, entry)
        if os.path.isfile(os.path.join(path, TEMPLATE_FILE)):
            try:
                templates.append(ProjectTemplate.load(path))
            except TemplateError as e:
                templates.append((path, e))
    return sorted(templates, key=lambda item: item.name.lower() if isinstance(item, ProjectTemplate) else item[0])

@dataclass
class TemplateRepository:
    url: str
    title: str

def template_repositories() -> list[TemplateRepository]:
    """Git repositories with templates from repositories.txt: lines with URL, optionally followed by title."""
    directory = templates_directory()
    path = os.path.join(directory, REPOSITORIES_FILE) if directory else None
    if not path or not os.path.isfile(path):
        return []
    repositories = []
    with open(path, encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            url, _, title = line.partition(" ")
            repositories.append(TemplateRepository(url=url, title=title.strip() or url.rstrip("/").split("/")[-1].removesuffix(".git")))
    return repositories

def _run_git(command: list[str], timeout: int = 300):
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"} # Fails instead of waiting for credentials.
    )
    if result.returncode != 0:
        output = result.stdout.strip()
        raise TemplateError(output.splitlines()[-1] if output else f"{' '.join(command[:2])} failed")

# Template repositories can contain only template: template.toml, files used by it and few common repository files.
_REPOSITORY_ALLOWED_FILES = re.compile(r"^(template\.toml|\.gitignore|\.gitattributes|(README|LICENSE|LICENCE|COPYING)(\.[A-Za-z0-9]+)?)$")
_REPOSITORY_FILES_DIRECTORY = "files"
_REPOSITORY_MAX_FILES = 2_000
_REPOSITORY_MAX_FILE_SIZE = 10 * 1024 * 1024
_REPOSITORY_MAX_TOTAL_SIZE = 50 * 1024 * 1024

def validate_template_repository(path: str, check_sizes: bool):
    """Checks that latest commit of template repository contains only template: template.toml in root, optionally
    README, LICENSE, COPYING, .gitignore and .gitattributes files, and other files in files directory. Symbolic links,
    submodules and executable files are not allowed. Sizes of files are checked when they were downloaded
    (check_sizes), listing of files doesn't need their contents."""
    command = ["git", "-C", path, "ls-tree", "-r", "-z"] + (["-l"] if check_sizes else []) + ["HEAD"]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if result.returncode != 0:
        raise TemplateError(f"Failed to list files of repository: {result.stderr.decode(errors='replace').strip()}")
    entries = [entry for entry in result.stdout.decode("utf-8", errors="replace").split("\0") if entry]
    if len(entries) > _REPOSITORY_MAX_FILES:
        raise TemplateError(f"Repository has more than {_REPOSITORY_MAX_FILES} files")
    has_template = False
    total_size = 0
    for entry in entries:
        details, _, file_path = entry.partition("\t")
        mode, object_type, *rest = details.split()
        if object_type == "commit":
            raise TemplateError(f"Repository can't contain submodules ({file_path})")
        if mode == "120000":
            raise TemplateError(f"Repository can't contain symbolic links ({file_path})")
        if mode != "100644":
            raise TemplateError(f"Repository can't contain executable or special files ({file_path})")
        if "/" in file_path:
            if file_path.split("/")[0] != _REPOSITORY_FILES_DIRECTORY:
                raise TemplateError(f"Files of template must be in {_REPOSITORY_FILES_DIRECTORY} directory ({file_path})")
        elif not _REPOSITORY_ALLOWED_FILES.match(file_path):
            raise TemplateError(f"Repository can contain only template ({file_path} is not allowed)")
        has_template = has_template or file_path == TEMPLATE_FILE
        if check_sizes:
            size = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 0
            if size > _REPOSITORY_MAX_FILE_SIZE:
                raise TemplateError(f"{file_path} is larger than {_REPOSITORY_MAX_FILE_SIZE // 1024 // 1024} MB")
            total_size += size
    if not has_template:
        raise TemplateError(f"Repository has no {TEMPLATE_FILE}")
    if total_size > _REPOSITORY_MAX_TOTAL_SIZE:
        raise TemplateError(f"Files of repository are larger than {_REPOSITORY_MAX_TOTAL_SIZE // 1024 // 1024} MB")

def fetch_template_repository(url: str) -> ProjectTemplate:
    """Downloads only template.toml from latest commit of template repository, to show its options. Other files are
    used from repository cloned when project is created. Uses partial clone without contents of files, servers that
    don't support it send whole latest commit."""
    from .repository import Repository
    temporary = os.path.realpath(os.path.expanduser(Repository.Settings.value.temporary_location))
    path = os.path.join(temporary, "Project templates", hashlib.sha1(url.encode()).hexdigest()[:16])
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _run_git(["git", "clone", "--depth", "1", "--filter=blob:none", "--no-checkout", "--quiet", url, path])
    validate_template_repository(path, check_sizes=False)
    _run_git(["git", "-C", path, "sparse-checkout", "set", "--no-cone", "/" + TEMPLATE_FILE])
    _run_git(["git", "-C", path, "checkout", "--quiet"])
    return ProjectTemplate.load(path, repository_url=url, files_available=False)

def load_cloned_template(path: str, repository_url: str) -> ProjectTemplate:
    """Template of repository cloned as project directory. Its files are copied to temporary directory first, as
    project directory is replaced with generated content. Remove returned template path when done."""
    from .repository import Repository
    validate_template_repository(path, check_sizes=True)
    temporary = os.path.realpath(os.path.expanduser(Repository.Settings.value.temporary_location))
    os.makedirs(temporary, exist_ok=True)
    copy_path = os.path.join(tempfile.mkdtemp(prefix="project-template-", dir=temporary), "template")
    shutil.copytree(path, copy_path, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    return ProjectTemplate.load(copy_path, repository_url=repository_url)

# ------------------------------------------------------------------------------
# Creating project.
# ------------------------------------------------------------------------------

def _copy_template_files(source: str, destination: str, names: dict[str, Any], log: Callable[[str], None]):
    """Copies file or directory, rendering .template files. Symbolic links are skipped, they could point outside of
    template."""
    if os.path.islink(source):
        log(f"Skipped symbolic link {source}")
        return
    if os.path.isdir(source):
        os.makedirs(destination, exist_ok=True)
        for entry in sorted(os.listdir(source)):
            if entry == ".git":
                continue
            _copy_template_files(os.path.join(source, entry), os.path.join(destination, entry), names, log)
        return
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    if destination.endswith(RENDERED_FILE_SUFFIX):
        with open(source, encoding="utf-8") as file:
            content = render_value(file.read(), names)
        with open(destination.removesuffix(RENDERED_FILE_SUFFIX), "w", encoding="utf-8") as file:
            file.write(str(content))
    else:
        shutil.copyfile(source, destination)

def _stage_argument_names(project_directory, target: str) -> set[str] | None:
    """Names of arguments of catalyst target (attribute names in stages), None when they can't be loaded."""
    from .project_stage import load_catalyst_stage_arguments_details
    try:
        arguments = load_catalyst_stage_arguments_details(toolset=project_directory.get_toolset(), target_name=target)
        return {argument.attribute_name for argument in arguments.values()}
    except Exception:
        return None

def _enable_stage_overlays(project_directory, stage, log: Callable[[str], None]):
    """Stage overlays (portage and root_overlay folders of stage) with files from template are used by stage, as
    stage overlay source of Portage confdir and root overlay."""
    from .project_stage_portage_confdir import (
        StagePortageConfdirSource, PORTAGE_CONFDIR_SOURCES_ORDER, stage_overlay_path, stage_root_overlay_path,
        root_overlay_argument, count_files, default_portage_confdir_sources
    )
    from .project_stage_arguments import StageArgumentDetails
    for path, attribute in ((stage_overlay_path(project_directory, stage), StageArgumentDetails.portage_confdir.name),
                            (stage_root_overlay_path(project_directory, stage), getattr(root_overlay_argument(stage), "name", None))):
        if attribute is None or not os.path.isdir(path) or count_files(path) == 0:
            continue
        value = getattr(stage, attribute, None)
        if not isinstance(value, list):
            value = default_portage_confdir_sources(has_parent=getattr(stage, "parent", None) is not None)
        sources = [item for item in value if isinstance(item, StagePortageConfdirSource)]
        if StagePortageConfdirSource.STAGE_OVERLAY not in sources:
            sources.append(StagePortageConfdirSource.STAGE_OVERLAY)
            setattr(stage, attribute, [source for source in PORTAGE_CONFDIR_SOURCES_ORDER if source in sources])
            log(f"Using {os.path.basename(path)} of {stage.name} from template")

def apply_project_template(project_directory, template: ProjectTemplate, names: dict[str, Any],
                           log: Callable[[str], None], replace_content: bool = False):
    """Creates files and stages of template in project directory. Project needs configuration (toolset, releng) set
    already, default arguments of stages are set like for stages added in app. With replace_content other files of
    directory (eg. cloned template repository) are removed first, Git directory is kept."""
    from .project_stage import ProjectStage, apply_default_stage_arguments
    from .project_manager import ProjectManager
    generated = template.generate(names)
    path = project_directory.directory_path()
    if replace_content:
        for entry in os.listdir(path):
            if entry == ".git":
                continue
            entry_path = os.path.join(path, entry)
            if os.path.isdir(entry_path) and not os.path.islink(entry_path):
                shutil.rmtree(entry_path)
            else:
                os.remove(entry_path)
    for source, destination in generated.files:
        # Template directory can be reached through symbolic link (eg. in macOS application bundle).
        log(f"Copying {os.path.relpath(source, os.path.realpath(template.path))} to {destination}")
        _copy_template_files(source, os.path.join(path, destination), names, log)
    os.makedirs(os.path.join(path, "stages"), exist_ok=True)
    if hasattr(project_directory, "_stages"):
        del project_directory._stages # Loaded again with files copied from template.
    releng_directory = project_directory.get_releng_directory()
    architecture = project_directory.get_architecture()
    stage_ids: dict[str, uuid.UUID] = {}
    for generated_stage in generated.stages:
        # Stage directory can exist already, with files copied from template.
        if any(existing.name == generated_stage.name for existing in project_directory.stages) or os.path.exists(
                os.path.join(project_directory.stage_directory_path(generated_stage.name), "stage.json")):
            raise TemplateError(f"Stage {generated_stage.name} already exists")
        stage = ProjectStage(
            parent_id=stage_ids[generated_stage.parent] if generated_stage.parent else None,
            name=generated_stage.name, target_name=generated_stage.target,
            releng_template_name=generated_stage.releng_template
        )
        valid_arguments = _stage_argument_names(project_directory, generated_stage.target)
        for key, value in generated_stage.arguments.items():
            if valid_arguments is not None and key not in valid_arguments:
                # Groups can set arguments of different targets (eg. stage4_packages and livecd_packages).
                continue
            setattr(stage, key, value)
        if generated_stage.releng_template and releng_directory and architecture:
            spec_path = os.path.join(releng_directory.directory_path(), "releases", "specs",
                                     architecture.releng_base_arch().value, generated_stage.releng_template)
            if not os.path.isfile(spec_path):
                log(f"Warning: releng template {generated_stage.releng_template} not found in {releng_directory.name}")
        try:
            apply_default_stage_arguments(project_directory=project_directory, stage=stage)
        except Exception as e:
            # Stage can still be configured manually.
            log(f"Failed to set default values of {stage.name}: {e}")
        _enable_stage_overlays(project_directory, stage, log)
        ProjectManager.shared().save_stage(project=project_directory, stage=stage)
        project_directory.stages.append(stage)
        stage_ids[generated_stage.template_id] = stage.id
        log(f"Created stage {stage.name} ({stage.target})")
