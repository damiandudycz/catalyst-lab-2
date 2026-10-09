from __future__ import annotations
import os
from datetime import datetime, timezone
from enum import Enum
from gi.repository import Gtk, Adw, GLib, Gio
from .project_directory import ProjectDirectory
from .project_build import StageBuildStatus, load_project_builds
from .project_build_process import ProjectBuildStepBuildStage, running_project_build
from .project_build_view import ProjectBuildView
from .multistage_process import MultiStageProcess, MultiStageProcessEvent, MultiStageProcessStageEvent, MultiStageProcessStageState
from .app_events import app_event_bus, AppEvents
from .helper_functions import get_file_size_string
from .deploy_installation import is_deployable
from .deploy_create_view import DeployCreateView

class BuildRowState(Enum):
    """State of stage in build run, as displayed."""
    COMPLETED = "Completed"
    FAILED = "Failed"
    BUILDING = "Building"
    SCHEDULED = "Scheduled"
    SKIPPED = "Skipped" # Stage it depends on failed.
    CANCELLED = "Cancelled" # Build was cancelled before stage started.
    INTERRUPTED = "Interrupted" # Not finished, but its build is not running anymore (eg. app was closed).
    NOT_STARTED = "Not started" # Scheduled, but its build is not running anymore (eg. app was closed).

class ProjectBuildsView(Gtk.Box):
    """Lists builds of project stages, grouped by build runs (stages built together). Shown from Builds section.
    Running build includes its scheduled stages, and its rows open build progress."""

    def __init__(self, project_directory: ProjectDirectory, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.project_directory = project_directory
        self.content_navigation_view = content_navigation_view
        self.page = Adw.PreferencesPage()
        self.page.set_vexpand(True)
        self.append(self.page)
        self.groups: list[Adw.PreferencesGroup] = []
        self._observed_build = None
        self._reload_scheduled = False
        # Builds can change while view is open (build finished), refresh when shown again.
        self.connect("map", lambda widget: self.load_builds())
        MultiStageProcess.event_bus.subscribe(MultiStageProcessEvent.STARTED_PROCESSES_CHANGED, self._on_started_processes_changed)

    # Loading builds
    # --------------------------------------------------------------------------

    def load_builds(self):
        for group in self.groups:
            self.page.remove(group)
        self.groups = []
        running_build = running_project_build(self.project_directory)
        self._observe_running_build(running_build)
        builds = load_project_builds(self.project_directory)
        if not builds and not running_build:
            group = Adw.PreferencesGroup()
            group.add(Adw.StatusPage(
                icon_name="box-minimalistic-svgrepo-com-symbolic",
                title="No builds yet",
                description="Build stages of this project from its Stages page."
            ))
            self._add_group(group)
            return
        # Stages built together share timestamp, group them as one build run. Newest runs first (builds are loaded
        # newest first). Running build is always first and contains also its stages that didn't start yet.
        stage_ids = {stage.id for stage in self.project_directory.stages}
        runs: dict[str, list] = {}
        if running_build:
            runs[running_build.timestamp] = []
        for build in builds:
            runs.setdefault(build.timestamp, []).append(build)
        for timestamp, run_builds in runs.items():
            if running_build and timestamp == running_build.timestamp:
                entries = self._running_build_entries(running_build, run_builds)
            else:
                # Stages in build order (older records without it by date).
                ordered = sorted(run_builds, key=lambda build: (build.order is None, build.order or 0, build.date))
                entries = [(build.stage_name, build, _record_state(build)) for build in ordered]
            is_running = running_build is not None and timestamp == running_build.timestamp
            title = f"Started {_format_timestamp(timestamp, fallback=entries[0][1].date if entries and entries[0][1] else datetime.now())}"
            group = Adw.PreferencesGroup(title=title, description=_run_summary([state for _, _, state in entries]))
            if not is_running:
                group.set_header_suffix(self._delete_button(
                    tooltip="Remove builds",
                    heading="Remove builds?",
                    body=(
                        f"All {len(run_builds)} builds started {title.removeprefix('Started ')} will be removed with their files. This can't be undone."
                        if len(run_builds) > 1 else
                        f"Build started {title.removeprefix('Started ')} will be removed with its files. This can't be undone."
                    ),
                    builds=run_builds
                ))
            for stage_name, build, state in entries:
                stage_removed = build is not None and build.stage_id not in stage_ids
                group.add(self._build_row(stage_name, build, state, stage_removed, running_build if is_running else None))
            self._add_group(group)

    def _running_build_entries(self, running_build, run_builds: list) -> list[tuple]:
        """Stages of running build in build order, with builds of the ones that started."""
        builds_by_stage = {build.stage_id: build for build in run_builds}
        steps_by_stage = {step.stage.id: step for step in running_build.stages if isinstance(step, ProjectBuildStepBuildStage)}
        entries = []
        for stage in running_build.plan.build_order():
            build = builds_by_stage.get(stage.id)
            step = steps_by_stage.get(stage.id)
            started = build is not None and build.status.is_attempt
            if started and build.status != StageBuildStatus.IN_PROGRESS:
                state = _record_state(build)
            elif started:
                state = BuildRowState.BUILDING
            elif step is not None and step.state == MultiStageProcessStageState.FAILED:
                state = BuildRowState.SKIPPED
            else:
                state = BuildRowState.SCHEDULED
            entries.append((stage.name, build, state))
        return entries

    def _add_group(self, group: Adw.PreferencesGroup):
        self.page.add(group)
        self.groups.append(group)

    def _build_row(self, stage_name: str, build, state: BuildRowState, stage_removed: bool, running_build) -> Adw.ActionRow:
        details = [state.value]
        if stage_removed:
            details.append("stage was removed from project")
        if build and build.artifact_path and os.path.isfile(build.artifact_path):
            details.append(build.artifact)
            if size := get_file_size_string(build.artifact_path):
                details.append(size)
        row = Adw.ActionRow(title=GLib.markup_escape_text(stage_name), subtitle=GLib.markup_escape_text(" · ".join(details)))
        icon = Gtk.Image.new_from_icon_name(_state_icon(state))
        icon.set_pixel_size(24)
        if css_class := _state_css_class(state):
            icon.add_css_class(css_class)
        row.add_prefix(icon)
        if build and not running_build and is_deployable(self.project_directory, build):
            button = Gtk.Button(icon_name="deploy-symbolic", tooltip_text="Deploy on another machine", valign=Gtk.Align.CENTER)
            button.add_css_class("flat")
            button.connect("clicked", lambda _, build=build: app_event_bus.emit(
                AppEvents.PRESENT_VIEW, DeployCreateView(project_directory=self.project_directory, build=build), "Deploy build", 640, 560))
            row.add_suffix(button)
        if build and build.path and os.path.isdir(build.path):
            button = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Open build folder", valign=Gtk.Align.CENTER)
            button.add_css_class("flat")
            button.connect("clicked", lambda _, path=build.path: Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(self.get_root(), None, None))
            row.add_suffix(button)
        if build and not running_build:
            row.add_suffix(self._delete_button(
                tooltip="Remove build",
                heading="Remove build?",
                body=f"Build of \"{stage_name}\" will be removed with its files. This can't be undone.",
                builds=[build]
            ))
        if running_build:
            # Stages of running build open progress of whole build.
            row.set_activatable(True)
            row.connect("activated", lambda _: self._open_build_progress(running_build))
            arrow = Gtk.Image.new_from_icon_name("go-next-symbolic")
            arrow.add_css_class("dimmed")
            row.add_suffix(arrow)
        return row

    # Removing builds
    # --------------------------------------------------------------------------

    def _delete_button(self, tooltip: str, heading: str, body: str, builds: list) -> Gtk.Button:
        button = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=tooltip, valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.connect("clicked", lambda _: self._confirm_delete(heading, body, builds))
        return button

    def _confirm_delete(self, heading: str, body: str, builds: list):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Remove")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _, response: self._delete_builds(builds) if response == "delete" else None)
        dialog.present(self.get_root())

    def _delete_builds(self, builds: list):
        for build in builds:
            try:
                build.delete()
            except Exception as e:
                print(f"Error removing build {build.path}: {e}")
        self.load_builds()

    def _open_build_progress(self, running_build):
        app_event_bus.emit(
            AppEvents.PRESENT_VIEW,
            ProjectBuildView(project_directory=self.project_directory, installation_in_progress=running_build),
            "Build stages", 640, 480
        )

    # Refreshing while build is running
    # --------------------------------------------------------------------------

    def _observe_running_build(self, running_build):
        if running_build is None or running_build is self._observed_build:
            return
        self._observed_build = running_build
        for step in running_build.stages:
            step.event_bus.subscribe(MultiStageProcessStageEvent.STATE_CHANGED, self._on_build_changed)

    def _on_build_changed(self, *args):
        self._schedule_reload()

    def _on_started_processes_changed(self, *args):
        self._schedule_reload()

    def _schedule_reload(self):
        # Several changes can come at once, reload once.
        if self._reload_scheduled or not self.get_mapped():
            return
        self._reload_scheduled = True
        def reload():
            self._reload_scheduled = False
            self.load_builds()
            return False
        GLib.idle_add(reload)

def _record_state(build) -> BuildRowState:
    match build.status:
        case StageBuildStatus.COMPLETED: return BuildRowState.COMPLETED
        case StageBuildStatus.FAILED: return BuildRowState.FAILED
        case StageBuildStatus.SKIPPED: return BuildRowState.SKIPPED
        case StageBuildStatus.CANCELLED: return BuildRowState.CANCELLED
        case StageBuildStatus.SCHEDULED: return BuildRowState.NOT_STARTED
    # Running builds are handled separately, so not finished build here is not running anymore.
    return BuildRowState.INTERRUPTED

def _format_timestamp(timestamp: str, fallback: datetime) -> str:
    """Build run timestamp (@TIMESTAMP@, in UTC) in local time."""
    try:
        date = datetime.strptime(timestamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone()
    except ValueError:
        date = fallback
    return date.strftime("%Y-%m-%d %H:%M")

def _run_summary(states: list[BuildRowState]) -> str:
    parts = [f"{len(states)} stage{'s' if len(states) != 1 else ''}"]
    for state in BuildRowState:
        if count := states.count(state):
            parts.append(f"{count} {state.value.lower()}")
    return ", ".join(parts)

def _state_icon(state: BuildRowState) -> str:
    match state:
        case BuildRowState.COMPLETED: return "check-square-svgrepo-com-symbolic"
        case BuildRowState.FAILED | BuildRowState.INTERRUPTED: return "error-box-svgrepo-com-symbolic"
        case BuildRowState.BUILDING: return "menu-dots-square-svgrepo-com-symbolic"
        case BuildRowState.SKIPPED | BuildRowState.CANCELLED | BuildRowState.NOT_STARTED: return "square-svgrepo-com-symbolic"
        case _: return "clock-square-svgrepo-com-symbolic"

def _state_css_class(state: BuildRowState) -> str | None:
    match state:
        case BuildRowState.COMPLETED: return "success"
        case BuildRowState.FAILED | BuildRowState.INTERRUPTED: return "error"
        case BuildRowState.BUILDING: return "accent"
        case BuildRowState.SCHEDULED | BuildRowState.SKIPPED | BuildRowState.CANCELLED | BuildRowState.NOT_STARTED: return "dimmed"
    return None
