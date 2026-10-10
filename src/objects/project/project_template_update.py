"""Updating projects created from templates.

When project is created from template, project-template.json is saved in project (template repository, its commit,
selected options and ids of stages created from template stages), and files generated from template are stored as
commit in Git repository of project (ref refs/catalystlab/template-base, not on its branch). This is base of update.

Update generates project again from latest version of template, with the same options and stage ids, and compares
three versions: base (generated before), project (current files, with not saved changes and local commits) and template
(generated now). Arguments of stages (stage.json) are compared one by one, other files as whole. Stages are matched by
ids, so renamed stages are compared too. Changes made only in template are applied, changes made only in project are
kept, and changes made in both differently are decided by user (keep project version or use template version).

Projects cloned from Git repository are updated the same way: base is the last commit shared with remote branch,
incoming version is the latest commit of remote branch. After changes are applied, the update is pending merge of
remote commit, so saving changes creates merge commit (history of project stays connected with remote).
"""
from __future__ import annotations
import json, os, shutil, subprocess, tempfile, uuid
from dataclasses import dataclass, field
from typing import Any, Callable
from .project_template import (
    ProjectTemplate, TemplateError, apply_project_template, validate_template_repository, _run_git
)

TEMPLATE_STATE_FILE = "project-template.json"
TEMPLATE_STATE_FORMAT = 1
BASE_REF = "refs/catalystlab/template-base"

# ------------------------------------------------------------------------------
# State of project created from template.
# ------------------------------------------------------------------------------

@dataclass
class TemplateState:
    repository_url: str
    repository_path: str # Directory of template in repository.
    template_name: str
    commit: str | None # Commit of template repository used to generate project.
    selected: dict[str, Any] # Values of options.
    stage_ids: dict[str, str] # Ids of project stages by ids of template stages.
    base: str | None # Commit with files generated from template (BASE_REF).

    @classmethod
    def load(cls, project_path: str) -> TemplateState | None:
        try:
            with open(os.path.join(project_path, TEMPLATE_STATE_FILE), encoding="utf-8") as file:
                data = json.load(file)
            return cls(repository_url=data["repository_url"], repository_path=data.get("repository_path", ""),
                       template_name=data.get("template_name", ""), commit=data.get("commit"),
                       selected=data.get("selected", {}), stage_ids=data.get("stage_ids", {}), base=data.get("base"))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, project_path: str):
        data = {
            "format": TEMPLATE_STATE_FORMAT,
            "repository_url": self.repository_url,
            "repository_path": self.repository_path,
            "template_name": self.template_name,
            "commit": self.commit,
            "selected": self.selected,
            "stage_ids": self.stage_ids,
            "base": self.base,
        }
        with open(os.path.join(project_path, TEMPLATE_STATE_FILE), "w", encoding="utf-8") as file:
            json.dump(data, file, indent=4)
            file.write("\n")

    @property
    def uuid_stage_ids(self) -> dict[str, uuid.UUID]:
        return {key: uuid.UUID(value) for key, value in self.stage_ids.items()}

def snapshot_directory(repository_path: str, directory: str, message: str) -> str | None:
    """Stores files of directory (without .git) as commit in Git repository of project, pointed by BASE_REF (not on
    branch of project). Returns its hash, None when project isn't Git repository yet."""
    if not os.path.isdir(os.path.join(repository_path, ".git")):
        return None
    git_directory = os.path.join(repository_path, ".git")
    with tempfile.TemporaryDirectory() as temporary:
        environment = {**os.environ, "GIT_INDEX_FILE": os.path.join(temporary, "index"),
                       "GIT_AUTHOR_NAME": "Catalyst Lab", "GIT_AUTHOR_EMAIL": "catalystlab@localhost",
                       "GIT_COMMITTER_NAME": "Catalyst Lab", "GIT_COMMITTER_EMAIL": "catalystlab@localhost"}
        def git(*arguments) -> str:
            result = subprocess.run(["git", f"--git-dir={git_directory}", f"--work-tree={directory}", *arguments],
                                    capture_output=True, text=True, env=environment)
            if result.returncode != 0:
                raise TemplateError(f"git {arguments[0]} failed: {result.stderr.strip()}")
            return result.stdout.strip()
        git("add", "--all", "--force", ".")
        tree = git("write-tree")
        commit = git("commit-tree", tree, "-m", message)
        git("update-ref", BASE_REF, commit)
        return commit

def record_template_state(project_path: str, template: ProjectTemplate, selected: dict[str, Any], commit: str | None,
                          stage_ids: dict[str, uuid.UUID]) -> TemplateState:
    """Saves state of project created (or updated) from template, and files generated from it as base of updates.
    Called when project directory contains only generated files."""
    state = TemplateState(repository_url=template.repository_url or "", repository_path=template.repository_path,
                          template_name=template.name, commit=commit, selected=selected,
                          stage_ids={key: str(value) for key, value in stage_ids.items()}, base=None)
    state.base = snapshot_directory(project_path, project_path, f"Generated from template {template.name}")
    state.save(project_path)
    return state

def repository_commit(path: str) -> str | None:
    result = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None

# ------------------------------------------------------------------------------
# Comparing versions.
# ------------------------------------------------------------------------------

_STAGES_DIRECTORY = "stages"
_STAGE_FILE = "stage.json"
_IGNORED = {".git", TEMPLATE_STATE_FILE, ".DS_Store"}

@dataclass
class ProjectVersion:
    """Files of project version by keys: "path" for files outside of stages, "stage:<id>/path" for files of stages,
    "stage:<id>#<argument>" for arguments of stages (values in stage.json format)."""
    entries: dict[str, Any] = field(default_factory=dict)
    stage_directories: dict[str, str] = field(default_factory=dict) # Directory names of stages by their ids.
    stage_names: dict[str, str] = field(default_factory=dict)

def read_project_version(root: str | None) -> ProjectVersion:
    version = ProjectVersion()
    if root is None or not os.path.isdir(root):
        return version
    stages_root = os.path.join(root, _STAGES_DIRECTORY)
    for directory, directories, files in os.walk(root):
        directories[:] = [name for name in directories if name not in _IGNORED]
        relative_directory = os.path.relpath(directory, root)
        parts = [] if relative_directory == "." else relative_directory.split(os.sep)
        if len(parts) == 2 and parts[0] == _STAGES_DIRECTORY and _STAGE_FILE in files:
            # Stage directory: its id is in stage.json.
            try:
                with open(os.path.join(directory, _STAGE_FILE), encoding="utf-8") as file:
                    stage = json.load(file)
                stage_id = stage["id"]["value"]
            except (OSError, ValueError, KeyError, TypeError):
                continue
            version.stage_directories[stage_id] = parts[1]
            version.stage_names[stage_id] = (stage.get("name") or {}).get("value", parts[1])
            for key, value in stage.items():
                version.entries[f"stage:{stage_id}#{key}"] = value
            for stage_directory, stage_subdirectories, stage_files in os.walk(directory):
                stage_subdirectories[:] = [name for name in stage_subdirectories if name not in _IGNORED]
                for name in stage_files:
                    path = os.path.join(stage_directory, name)
                    relative = os.path.relpath(path, directory)
                    if relative == _STAGE_FILE or name in _IGNORED or os.path.islink(path):
                        continue
                    with open(path, "rb") as file:
                        version.entries[f"stage:{stage_id}/{relative}"] = file.read()
            directories[:] = [] # Files of stage were read.
            continue
        if parts and parts[0] == _STAGES_DIRECTORY:
            continue # Directory of stage without stage.json, or stages directory itself.
        for name in files:
            path = os.path.join(directory, name)
            if name in _IGNORED or os.path.islink(path):
                continue
            with open(path, "rb") as file:
                version.entries[os.path.relpath(path, root)] = file.read()
    return version

_MISSING = object()

@dataclass
class TemplateChange:
    key: str
    title: str # Stage and argument or path, for user.
    base: Any
    project: Any
    template: Any
    conflict: bool # Changed in project and in template differently, user decides.
    use_template: bool # Decision, template version is used (automatic changes always use it).
    parts: list[TemplateChange] = field(default_factory=list) # Changes grouped in this one (eg. files of new stage).

    def flattened(self) -> list[TemplateChange]:
        return [part for change in self.parts for part in change.flattened()] if self.parts else [self]

def describe_value(value) -> str:
    if value is _MISSING or value is None:
        return "Not set"
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8").strip()
        except UnicodeDecodeError:
            return f"Binary file ({len(value)} bytes)"
        lines = text.splitlines()
        return (lines[0] + (f" … (+{len(lines) - 1} lines)" if len(lines) > 1 else "")) if lines else "Empty file"
    if isinstance(value, dict) and "value" in value:
        inner = value["value"]
        if isinstance(inner, list):
            return ", ".join(describe_value(item) for item in inner) or "Empty list"
        if isinstance(inner, dict):
            return str(inner.get("path") or inner)
        return str(inner)
    return str(value)

def compare_versions(base: ProjectVersion, project: ProjectVersion, template: ProjectVersion) -> list[TemplateChange]:
    """Changes of template to apply to project: made only in template (automatic) or made differently in project and
    template (conflicts)."""
    names = {**base.stage_names, **template.stage_names, **project.stage_names}
    changes = []
    for key in sorted(set(base.entries) | set(project.entries) | set(template.entries)):
        base_value = base.entries.get(key, _MISSING)
        project_value = project.entries.get(key, _MISSING)
        template_value = template.entries.get(key, _MISSING)
        if project_value == template_value or base_value == template_value:
            continue # Same in both, or template didn't change it (project version is kept).
        if key.startswith("stage:"):
            body = key[len("stage:"):]
            stage_id, _, rest = body.partition("#") if "#" in body else body.partition("/")
            title = f"{names.get(stage_id, stage_id)}: {rest}"
        else:
            title = key
        conflict = project_value != base_value
        changes.append(TemplateChange(key=key, title=title, base=base_value, project=project_value,
                                      template=template_value, conflict=conflict, use_template=not conflict))
    return _group_stage_changes(changes, project, template)

def _stage_id_of(key: str) -> str | None:
    if not key.startswith("stage:"):
        return None
    body = key[len("stage:"):]
    return body.partition("#")[0] if "#" in body else body.partition("/")[0]

def _group_stage_changes(changes: list[TemplateChange], project: ProjectVersion, template: ProjectVersion) -> list[TemplateChange]:
    """Automatic changes of stages added to project (only in template) or removed from it (not in template anymore)
    are shown as one change."""
    grouped, by_stage = [], {}
    for change in changes:
        stage_id = _stage_id_of(change.key)
        added = stage_id is not None and stage_id not in project.stage_directories and stage_id in template.stage_directories
        removed = stage_id is not None and stage_id in project.stage_directories and stage_id not in template.stage_directories
        if not change.conflict and (added or removed):
            by_stage.setdefault((stage_id, added), []).append(change)
        else:
            grouped.append(change)
    for (stage_id, added), parts in by_stage.items():
        name = (template if added else project).stage_names.get(stage_id, stage_id)
        grouped.append(TemplateChange(key=f"stage:{stage_id}", title=f"{'New' if added else 'Removed'} stage {name}",
                                      base=_MISSING, project=_MISSING if added else "Stage", template="Stage" if added else _MISSING,
                                      conflict=False, use_template=True, parts=parts))
    return grouped

def apply_changes(project_path: str, changes: list[TemplateChange], project: ProjectVersion, template: ProjectVersion):
    """Writes template versions of changes that use them to project."""
    def stage_directory(stage_id: str) -> str:
        name = project.stage_directories.get(stage_id) or template.stage_directories.get(stage_id) or stage_id
        return os.path.join(project_path, _STAGES_DIRECTORY, name)
    stage_files: dict[str, dict] = {}
    for change in [part for change in changes if change.use_template for part in change.flattened()]:
        if not change.use_template:
            continue
        if change.key.startswith("stage:") and "#" in change.key:
            stage_id, _, argument = change.key[len("stage:"):].partition("#")
            if stage_id not in stage_files:
                path = os.path.join(stage_directory(stage_id), _STAGE_FILE)
                try:
                    with open(path, encoding="utf-8") as file:
                        stage_files[stage_id] = json.load(file)
                except (OSError, ValueError):
                    stage_files[stage_id] = {}
            if change.template is _MISSING:
                stage_files[stage_id].pop(argument, None)
            else:
                stage_files[stage_id][argument] = change.template
            continue
        if change.key.startswith("stage:"):
            stage_id, _, relative = change.key[len("stage:"):].partition("/")
            path = os.path.join(stage_directory(stage_id), relative)
        else:
            path = os.path.join(project_path, change.key)
        if change.template is _MISSING:
            if os.path.isfile(path):
                os.remove(path)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as file:
                file.write(change.template)
    for stage_id, stage in stage_files.items():
        directory = stage_directory(stage_id)
        if not stage:
            # Stage removed from template.
            if os.path.isfile(os.path.join(directory, _STAGE_FILE)):
                os.remove(os.path.join(directory, _STAGE_FILE))
            continue
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, _STAGE_FILE), "w", encoding="utf-8") as file:
            json.dump(stage, file, indent=4)
        # Directory follows name of stage (renamed in template).
        name = (stage.get("name") or {}).get("value")
        if name:
            from .git_directory import GitDirectory
            renamed = os.path.join(project_path, _STAGES_DIRECTORY, GitDirectory.sanitized_name_for_name(name))
            if renamed != directory and not os.path.exists(renamed):
                os.rename(directory, renamed)
    # Directories of removed stages and files.
    for directory, directories, files in os.walk(os.path.join(project_path, _STAGES_DIRECTORY), topdown=False):
        if not directories and not files and os.path.abspath(directory) != os.path.abspath(os.path.join(project_path, _STAGES_DIRECTORY)):
            os.rmdir(directory)

# ------------------------------------------------------------------------------
# Update.
# ------------------------------------------------------------------------------

class _RenderTarget:
    """Project generated into other directory (template version), with configuration of real project."""
    def __init__(self, project_directory, path: str):
        self._project = project_directory
        self._path = path
        self._stages = [] # Created stages, cleared by apply_project_template.
        class _NoEvents:
            def emit(self, *arguments):
                pass
            def subscribe(self, *arguments, **keywords):
                pass
        self.event_bus = _NoEvents()
    def directory_path(self) -> str:
        return self._path
    def stage_directory_path(self, name: str) -> str:
        return os.path.join(self._path, _STAGES_DIRECTORY, self._project.sanitized_name_for_name(name))
    @property
    def stages(self):
        if "_stages" not in self.__dict__:
            self.__dict__["_stages"] = []
        return self.__dict__["_stages"]
    def __getattr__(self, name):
        return getattr(self._project, name)

@dataclass
class TemplateUpdate:
    """Update prepared for project: changes, and template version to apply."""
    state: TemplateState
    template: ProjectTemplate
    commit: str | None
    stage_ids: dict[str, uuid.UUID]
    changes: list[TemplateChange]
    project_version: ProjectVersion
    template_version: ProjectVersion
    temporary_directory: str

    def cleanup(self):
        shutil.rmtree(self.temporary_directory, ignore_errors=True)

    @property
    def source_title(self) -> str:
        return "template"

    def apply(self, project_directory):
        apply_template_update(project_directory, self)

def prepare_template_update(project_directory, log: Callable[[str], None]) -> TemplateUpdate:
    """Downloads latest version of template, generates project from it and compares it with project and its base."""
    from .repository import Repository
    project_path = project_directory.directory_path()
    state = TemplateState.load(project_path)
    if state is None:
        raise TemplateError("Project wasn't created from template")
    temporary_root = os.path.realpath(os.path.expanduser(Repository.Settings.value.temporary_location))
    os.makedirs(temporary_root, exist_ok=True)
    temporary = tempfile.mkdtemp(prefix="template-update-", dir=temporary_root)
    try:
        # Latest template:
        repository = os.path.join(temporary, "repository")
        log(f"Downloading {state.repository_url}")
        _run_git(["git", "clone", "--depth", "1", "--quiet", state.repository_url, repository])
        if state.repository_path not in validate_template_repository(repository, check_sizes=True):
            raise TemplateError(f"Template {state.repository_path or state.template_name} is not in repository anymore")
        template = ProjectTemplate.load(os.path.join(repository, state.repository_path),
                                        repository_url=state.repository_url, repository_path=state.repository_path)
        commit = repository_commit(repository)
        # Template version of project, with the same options and stage ids:
        generated = os.path.join(temporary, "template")
        os.makedirs(generated)
        names = template.resolve(state.selected, project_name=project_directory.name)
        log(f"Generating project from {template.name}")
        stage_ids = apply_project_template(_RenderTarget(project_directory, generated), template, names, log=log,
                                           stage_ids=state.uuid_stage_ids)
        # Base (generated before), stored in project repository:
        base = os.path.join(temporary, "base")
        os.makedirs(base)
        if state.base and _extract_commit(project_path, state.base, base):
            base_version = read_project_version(base)
        else:
            log("Files generated before are not available, all differences have to be decided")
            base_version = ProjectVersion()
        project_version = read_project_version(project_path)
        template_version = read_project_version(generated)
        changes = compare_versions(base_version, project_version, template_version)
        return TemplateUpdate(state=state, template=template, commit=commit, stage_ids=stage_ids, changes=changes,
                              project_version=project_version, template_version=template_version,
                              temporary_directory=temporary)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

def _extract_commit(repository_path: str, commit: str, destination: str) -> bool:
    archive = subprocess.run(["git", "-C", repository_path, "archive", commit], capture_output=True)
    if archive.returncode != 0:
        return False
    extract = subprocess.run(["tar", "-x", "-C", destination], input=archive.stdout, capture_output=True)
    return extract.returncode == 0

def apply_template_update(project_directory, update: TemplateUpdate):
    """Applies changes (with decisions of user) to project, as not saved changes. Template version becomes base of
    next update."""
    project_path = project_directory.directory_path()
    apply_changes(project_path, update.changes, update.project_version, update.template_version)
    generated = os.path.join(update.temporary_directory, "template")
    state = update.state
    state.commit = update.commit
    state.template_name = update.template.name
    state.stage_ids = {key: str(value) for key, value in update.stage_ids.items()}
    state.base = snapshot_directory(project_path, generated, f"Generated from template {update.template.name}") or state.base
    state.save(project_path)
    if hasattr(project_directory, "_stages"):
        del project_directory._stages # Stages are read again.
    from .git_directory import GitDirectoryEvent
    project_directory.event_bus.emit(GitDirectoryEvent.CONTENT_CHANGED, project_directory)

# ------------------------------------------------------------------------------
# Projects cloned from Git repository.
# ------------------------------------------------------------------------------

def _git(path: str, *arguments, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", path, *arguments], capture_output=True, text=True,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if check and result.returncode != 0:
        raise TemplateError(f"git {arguments[0]} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()

@dataclass
class RepositoryOrigin:
    url: str
    branch: str | None

def repository_origin(project_path: str) -> RepositoryOrigin | None:
    """Remote repository of project cloned from Git repository (not created from template)."""
    if TemplateState.load(project_path) is not None or not os.path.isdir(os.path.join(project_path, ".git")):
        return None
    url = _git(project_path, "remote", "get-url", "origin", check=False)
    if not url:
        return None
    branch = _git(project_path, "symbolic-ref", "--short", "HEAD", check=False) or None
    return RepositoryOrigin(url=url, branch=branch)

def pending_repository_update(project_path: str) -> bool:
    """Update was applied, but not saved yet (merge is in progress)."""
    return os.path.exists(os.path.join(project_path, ".git", "MERGE_HEAD"))

@dataclass
class RepositoryUpdate:
    origin: RepositoryOrigin
    upstream: str # Latest commit of remote branch.
    changes: list[TemplateChange]
    project_version: ProjectVersion
    template_version: ProjectVersion # Version from remote.
    temporary_directory: str
    fast_forward: bool # No local changes or commits, branch is moved to remote commit.

    def cleanup(self):
        shutil.rmtree(self.temporary_directory, ignore_errors=True)

    @property
    def source_title(self) -> str:
        return "repository"

    def apply(self, project_directory):
        apply_repository_update(project_directory, self)

def prepare_repository_update(project_directory, log: Callable[[str], None]) -> RepositoryUpdate | None:
    """Downloads changes of remote branch and compares them with project. None when there is nothing new."""
    from .repository import Repository
    project_path = project_directory.directory_path()
    origin = repository_origin(project_path)
    if origin is None:
        raise TemplateError("Project wasn't cloned from Git repository")
    if pending_repository_update(project_path):
        raise TemplateError("Previous update is not saved yet, save or discard changes first")
    log(f"Fetching {origin.url}")
    _git(project_path, "fetch", "--quiet", "origin")
    upstream = _git(project_path, "rev-parse", "@{upstream}", check=False) or _git(project_path, "rev-parse", "origin/HEAD")
    head = _git(project_path, "rev-parse", "HEAD")
    merge_base = _git(project_path, "merge-base", "HEAD", upstream)
    if merge_base == upstream:
        return None # Remote has nothing new.
    temporary_root = os.path.realpath(os.path.expanduser(Repository.Settings.value.temporary_location))
    os.makedirs(temporary_root, exist_ok=True)
    temporary = tempfile.mkdtemp(prefix="repository-update-", dir=temporary_root)
    try:
        base, remote = os.path.join(temporary, "base"), os.path.join(temporary, "remote")
        os.makedirs(base)
        os.makedirs(remote)
        if not _extract_commit(project_path, merge_base, base) or not _extract_commit(project_path, upstream, remote):
            raise TemplateError("Failed to read versions of repository")
        project_version = read_project_version(project_path)
        remote_version = read_project_version(remote)
        changes = compare_versions(read_project_version(base), project_version, remote_version)
        clean = not _git(project_path, "status", "--porcelain")
        return RepositoryUpdate(origin=origin, upstream=upstream, changes=changes, project_version=project_version,
                                template_version=remote_version, temporary_directory=temporary,
                                fast_forward=clean and merge_base == head)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

def apply_repository_update(project_directory, update: RepositoryUpdate):
    """Moves branch to remote commit when there are no local changes, otherwise applies changes (with decisions of
    user) and starts merge of remote commit, finished by saving changes."""
    project_path = project_directory.directory_path()
    if update.fast_forward:
        _git(project_path, "merge", "--ff-only", "--quiet", update.upstream)
    else:
        apply_changes(project_path, update.changes, update.project_version, update.template_version)
        with open(os.path.join(project_path, ".git", "MERGE_HEAD"), "w", encoding="utf-8") as file:
            file.write(update.upstream + "\n")
        with open(os.path.join(project_path, ".git", "MERGE_MSG"), "w", encoding="utf-8") as file:
            file.write(f"Merge updates from {update.origin.url}\n")
    if hasattr(project_directory, "_stages"):
        del project_directory._stages
    from .git_directory import GitDirectoryEvent
    project_directory.event_bus.emit(GitDirectoryEvent.CONTENT_CHANGED, project_directory)
    project_directory.update_logs()
