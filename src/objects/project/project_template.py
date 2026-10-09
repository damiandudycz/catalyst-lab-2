"""Project templates: directories with template.toml describing stages (and optional files) of new project.

Local templates are directories in data/project_templates of the app, templates from Git repositories have the same
layout in root of repository. Repositories offered in the app are listed in data/project_templates/repositories.txt.

Templates can't run code. Values in template.toml can contain {{ expressions }} and "when" conditions, evaluated by
small evaluator that only allows constants, template variables, operators, conditional expressions and few functions
(see TemplateExpression), so templates from other repositories are safe to use.

template.toml:

    format = 1
    name = "Name of template"
    description = "Shown in list of templates"

    [[variables]]                        # Options selected when creating project, in this order.
    id = "init"                          # Name used in expressions.
    title = "Init system"
    description = "..."                  # Optional.
    type = "choice"                      # choice (default), boolean or architecture (sets architecture of project).
    default = "openrc"                   # Optional, first available option otherwise.
    when = "expression"                  # Optional, variable is not used (its value is none) when false.
    options = [                          # Not used for boolean variables.
        { value = "openrc", title = "OpenRC" },
        { value = "systemd", title = "systemd", when = "expression" },  # Option available only when true.
    ]

    [values]                             # Optional values computed from variables, in this order.
    profile_name = "{{ 'systemd' if init == 'systemd' else 'openrc' }}"

    [[stages]]                           # Stages of project, parents before their children.
    id = "stage1"                        # Used in parent of other stages.
    name = "stage1-{{ init }}"
    target = "stage1"                    # Catalyst target.
    parent = "..."                       # Optional, id of parent stage (seed).
    releng_template = "stage1-{{ init }}-23.spec"  # Optional, path relative to specs of architecture in releng.
    when = "expression"                  # Optional, stage is created only when true.
    [stages.arguments]                   # Optional values of stage arguments, others get default automatic values.
    stage4_packages = ["app-admin/sudo"] # Strings, lists of strings, booleans and numbers, or { type, value } tables
                                         # stored in stage.json format. Empty values are skipped.

    [[files]]                            # Optional files copied to project (eg. portage configuration of stages).
    source = "files/stage4"              # File or directory in template.
    destination = "stages/{{ stage4_name }}"  # Path in project.
    when = "expression"                  # Optional.

Files with .template suffix are copied with {{ expressions }} in their contents replaced, and without that suffix.
Expressions can use variables, computed values and project_name.
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
_RESERVED_NAMES = {"project_name", "true", "false", "none"} | set(_FUNCTIONS)
_RESERVED_STAGE_ARGUMENTS = {"id", "parent", "target", "name", "releng_template", "event_bus"}

class TemplateVariableType:
    CHOICE = "choice"
    BOOLEAN = "boolean"
    ARCHITECTURE = "architecture" # Choice of architecture names, selected value is also architecture of project.
    ALL = (CHOICE, BOOLEAN, ARCHITECTURE)

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

@dataclass
class ProjectTemplate:
    path: str # Directory with template.toml.
    name: str
    description: str
    variables: list[TemplateVariable]
    values: dict[str, Any]
    stages: list[TemplateStage]
    files: list[TemplateFiles]
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
            if default is not None and default not in [option.value for option in options]:
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
        # Files:
        files = []
        for index, files_data in enumerate(data.get("files", [])):
            context = f"Files {index + 1}"
            if not isinstance(files_data, dict):
                raise TemplateError(f"{context} must be a table")
            files.append(TemplateFiles(
                source=_text(files_data, "source", context), destination=_text(files_data, "destination", context),
                when=_condition(files_data, context)
            ))
        return cls(path=path, name=name, description=description, variables=variables, values=values, stages=stages,
                   files=files, repository_url=repository_url, files_available=files_available)

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
            if not options:
                raise TemplateError(f"Variable {variable.id} has no available options")
            value = selected.get(variable.id)
            if value not in options:
                value = variable.default if variable.default in options else options[0]
            names[variable.id] = value
        for key, expression in self.values.items():
            names[key] = render_value(expression, names)
        return names

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
            stages.append(GeneratedStage(template_id=stage.id, name=name, target=target, parent=stage.parent,
                                         releng_template=releng_template, arguments=arguments))
            generated_ids.add(stage.id)
        files = []
        template_root = os.path.realpath(self.path)
        for entry in self.files:
            if not evaluate_condition(entry.when, names):
                continue
            source = os.path.realpath(os.path.join(template_root, str(render_value(entry.source, names))))
            destination = os.path.normpath(str(render_value(entry.destination, names)))
            if not source.startswith(template_root + os.sep):
                raise TemplateError(f"Files source {entry.source} is outside of template")
            if os.path.isabs(destination) or destination == ".." or destination.startswith(".." + os.sep) \
                    or destination.split(os.sep)[0] == ".git":
                raise TemplateError(f"Files destination {entry.destination} is outside of project")
            if self.files_available and not os.path.exists(source):
                raise TemplateError(f"Files source {entry.source} doesn't exist")
            files.append((source, destination))
        architecture_variable = self.architecture_variable
        architecture_name = names.get(architecture_variable.id) if architecture_variable else None
        return GeneratedProject(stages=stages, files=files,
                                architecture=Architecture[architecture_name] if architecture_name else None)

def _stage_argument_value(value, context: str):
    """Value of stage argument from template: texts, numbers, booleans and their lists are used directly, tables
    with type and value use format of stage.json."""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_stage_argument_value(item, context) for item in value]
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
    _run_git(["git", "-C", path, "sparse-checkout", "set", "--no-cone", "/" + TEMPLATE_FILE])
    _run_git(["git", "-C", path, "checkout", "--quiet"])
    return ProjectTemplate.load(path, repository_url=url, files_available=False)

def load_cloned_template(path: str, repository_url: str) -> ProjectTemplate:
    """Template of repository cloned as project directory. Its files are copied to temporary directory first, as
    project directory is replaced with generated content. Remove returned template path when done."""
    from .repository import Repository
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
        log(f"Copying {os.path.relpath(source, template.path)} to {destination}")
        _copy_template_files(source, os.path.join(path, destination), names, log)
    os.makedirs(os.path.join(path, "stages"), exist_ok=True)
    if hasattr(project_directory, "_stages"):
        del project_directory._stages # Loaded again with files copied from template.
    releng_directory = project_directory.get_releng_directory()
    architecture = project_directory.get_architecture()
    stage_ids: dict[str, uuid.UUID] = {}
    for generated_stage in generated.stages:
        if not ProjectManager.shared().is_stage_name_available(project=project_directory, name=generated_stage.name):
            raise TemplateError(f"Stage {generated_stage.name} already exists")
        stage = ProjectStage(
            parent_id=stage_ids[generated_stage.parent] if generated_stage.parent else None,
            name=generated_stage.name, target_name=generated_stage.target,
            releng_template_name=generated_stage.releng_template
        )
        for key, value in generated_stage.arguments.items():
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
        ProjectManager.shared().save_stage(project=project_directory, stage=stage)
        project_directory.stages.append(stage)
        stage_ids[generated_stage.template_id] = stage.id
        log(f"Created stage {stage.name} ({stage.target})")
