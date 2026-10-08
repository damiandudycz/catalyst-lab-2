from __future__ import annotations
import os, json, uuid
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
from .repository import Repository
from .project_stage_arguments import StageArgumentDetails

# ------------------------------------------------------------------------------
# Stage builds storage:
# <builds_location>/<project>/stages/<stage>/<timestamp>/build.json + build results.
# <builds_location>/<project> is also used as catalyst builds directory when building, so it contains catalyst output
# (moved to stage build directory after build), downloaded seeds (seeds/) and generated specs (work/).

class StageBuildStatus(Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class StageBuild:
    """Record of single stage build, stored in build.json inside its build directory."""
    stage_id: uuid.UUID
    stage_name: str
    timestamp: str # Value of @TIMESTAMP@ used for this build, also name of build directory.
    status: StageBuildStatus = StageBuildStatus.IN_PROGRESS
    date: datetime = field(default_factory=datetime.now)
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    path: str | None = None # Build directory, set when loaded or saved.
    artifact: str | None = None # Filename of built stage archive, inside build directory.

    METADATA_FILE = "build.json"

    @property
    def is_usable(self) -> bool:
        """Can be used as seed for other stages."""
        return self.status == StageBuildStatus.COMPLETED

    def serialize(self) -> dict:
        return {
            "id": str(self.id),
            "stage_id": str(self.stage_id),
            "stage_name": self.stage_name,
            "timestamp": self.timestamp,
            "status": self.status.value,
            "date": self.date.isoformat(),
            "artifact": self.artifact,
        }

    @classmethod
    def init_from(cls, data: dict, path: str | None = None) -> StageBuild:
        return cls(
            id=uuid.UUID(data["id"]),
            stage_id=uuid.UUID(data["stage_id"]),
            stage_name=data["stage_name"],
            timestamp=data["timestamp"],
            status=StageBuildStatus(data["status"]),
            date=datetime.fromisoformat(data["date"]),
            path=path,
            artifact=data.get("artifact"),
        )

    @property
    def artifact_path(self) -> str | None:
        return os.path.join(self.path, self.artifact) if self.path and self.artifact else None

    def save(self, project_directory):
        self.path = self.path or os.path.join(stage_builds_directory(project_directory, self.stage_name), self.timestamp)
        os.makedirs(self.path, exist_ok=True)
        with open(os.path.join(self.path, StageBuild.METADATA_FILE), "w", encoding="utf-8") as file:
            json.dump(self.serialize(), file, indent=4)

def builds_location() -> str:
    return os.path.realpath(os.path.expanduser(Repository.Settings.value.builds_location))

def project_builds_directory(project_directory) -> str:
    return os.path.join(builds_location(), project_directory.sanitized_name())

def stage_builds_directory(project_directory, stage_name: str) -> str:
    return os.path.join(project_builds_directory(project_directory), "stages", project_directory.sanitized_name_for_name(stage_name))

def load_project_builds(project_directory) -> list[StageBuild]:
    """All builds of project stages, newest first. Builds are matched to stages by id, so renamed stages keep them."""
    builds = []
    root = os.path.join(project_builds_directory(project_directory), "stages")
    if not os.path.isdir(root):
        return builds
    for stage_directory in os.scandir(root):
        if not stage_directory.is_dir():
            continue
        for build_directory in os.scandir(stage_directory.path):
            metadata_path = os.path.join(build_directory.path, StageBuild.METADATA_FILE)
            try:
                with open(metadata_path, encoding="utf-8") as file:
                    builds.append(StageBuild.init_from(json.load(file), path=build_directory.path))
            except (OSError, ValueError, KeyError) as e:
                if os.path.exists(metadata_path):
                    print(f"Warning: Failed to read build {metadata_path}: {e}")
    return sorted(builds, key=lambda build: build.date, reverse=True)

# ------------------------------------------------------------------------------
# Build plan:

class StageBuildMode(Enum):
    NONE = "none"           # Not used in this build.
    BUILD = "build"         # Selected to be built.
    REQUIRED = "required"   # Needed as seed by selected stage and has no usable build, so it's built too.
    REUSE = "reuse"         # Needed as seed by selected stage, its previous build is used.

@dataclass
class StageBuildPlanEntry:
    stage: object
    mode: StageBuildMode
    reused_build: StageBuild | None = None # For REUSE.
    required_by: list = field(default_factory=list) # Selected stages that need this one as seed (REQUIRED, REUSE).

    @property
    def is_built(self) -> bool:
        return self.mode in (StageBuildMode.BUILD, StageBuildMode.REQUIRED)

class StageBuildPlan:
    """Decides what happens with every stage of project when building selected stages. Stage needs its parent built
    first: either reused from previous build, or built in the same run. Parents without usable builds are built too,
    up to the root of branch."""

    def __init__(self, project_directory, selected_stage_ids: set[uuid.UUID], builds: list[StageBuild] | None = None):
        self.project_directory = project_directory
        self.builds = load_project_builds(project_directory) if builds is None else builds
        self.entries: dict[uuid.UUID, StageBuildPlanEntry] = {
            stage.id: StageBuildPlanEntry(stage=stage, mode=StageBuildMode.BUILD if stage.id in selected_stage_ids else StageBuildMode.NONE)
            for stage in project_directory.stages
        }
        for stage_id in selected_stage_ids:
            if stage_id in self.entries:
                self._resolve_ancestors(self.entries[stage_id].stage)

    def latest_build(self, stage) -> StageBuild | None:
        """Latest build that can be used as seed (completed, with its archive still present)."""
        return next((
            build for build in self.builds
            if build.stage_id == stage.id and build.is_usable and build.artifact_path and os.path.isfile(build.artifact_path)
        ), None)

    def latest_attempt(self, stage) -> StageBuild | None:
        """Latest build, including failed and unfinished ones."""
        return next((build for build in self.builds if build.stage_id == stage.id), None)

    def parent(self, stage):
        parent_id = getattr(stage, StageArgumentDetails.parent.name, None)
        return self.entries[parent_id].stage if parent_id in self.entries else None

    def _resolve_ancestors(self, selected_stage):
        stage, visited = selected_stage, {selected_stage.id}
        while (parent := self.parent(stage)) is not None and parent.id not in visited:
            visited.add(parent.id)
            entry = self.entries[parent.id]
            if entry.mode == StageBuildMode.BUILD:
                return # Built in this run, its own ancestors are resolved when processing it.
            entry.required_by.append(selected_stage)
            if entry.mode in (StageBuildMode.REUSE, StageBuildMode.REQUIRED):
                return # Already resolved for other selected stage.
            if latest := self.latest_build(parent):
                entry.mode = StageBuildMode.REUSE
                entry.reused_build = latest
                return # Previous build is used as seed, no need to go further.
            entry.mode = StageBuildMode.REQUIRED
            stage = parent

    def build_order(self) -> list:
        """Stages that are built, parents before their children."""
        order, added = [], set()
        def add(stage):
            if stage.id in added:
                return
            if (parent := self.parent(stage)) is not None and self.entries[parent.id].is_built:
                add(parent)
            added.add(stage.id)
            order.append(stage)
        for entry in self.entries.values():
            if entry.is_built:
                add(entry.stage)
        return order
