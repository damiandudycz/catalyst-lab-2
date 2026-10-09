from __future__ import annotations
import os
from datetime import datetime, timezone
from enum import Enum
from gi.repository import Gtk, Adw, GLib, Gio
from .project_directory import ProjectDirectory
from .project_build import StageBuildStatus, load_project_builds
from .project_build_process import ProjectBuild, ProjectBuildStepBuildStage, running_project_build
from .project_build_view import ProjectBuildView
from .multistage_process import MultiStageProcess, MultiStageProcessEvent, MultiStageProcessStageEvent, MultiStageProcessStageState, MultiStageProcessState
from .app_events import app_event_bus, AppEvents
from .helper_functions import get_file_size_string
from .project_build_details import failure_details, format_duration
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
        # Same layout as other views: groups use whole width of window.
        scrolled_window = Gtk.ScrolledWindow(hexpand=True, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_start=24, margin_end=24, margin_bottom=24)
        scrolled_window.set_child(self.content)
        self.append(scrolled_window)
        # Starts new build of project, or shows progress of running one (like Create build in project page).
        actions_group = Adw.PreferencesGroup()
        self.build_row = Adw.ButtonRow(title="Start new build", start_icon_name="sledgehammer-svgrepo-com-symbolic")
        self.build_row.connect("activated", self._on_build_activated)
        actions_group.add(self.build_row)
        self.content.append(actions_group)
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
            self.content.remove(group)
        self.groups = []
        running_build = running_project_build(self.project_directory)
        self._observe_running_build(running_build)
        self._update_build_row(running_build)
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
        self.content.append(group)
        self.groups.append(group)

    def _build_row(self, stage_name: str, build, state: BuildRowState, stage_removed: bool, running_build) -> Adw.ActionRow:
        details = [state.value]
        if stage_removed:
            details.append("stage was removed from project")
        if build and build.duration:
            details.append(format_duration(build.duration))
        if build and build.artifact_path and os.path.isfile(build.artifact_path):
            details.append(build.artifact)
            if size := get_file_size_string(build.artifact_path):
                details.append(size)
            if packages := (build.details.get("output") or {}).get("packages"):
                details.append(f"{packages} packages")
        subtitle = " · ".join(details)
        if state == BuildRowState.FAILED and (failure := _failure_summary(build)):
            subtitle += f"\n{failure}"
        row = Adw.ActionRow(title=GLib.markup_escape_text(stage_name), subtitle=GLib.markup_escape_text(subtitle))
        if build and (build.details.get("failure") or {}).get("logs"):
            row.set_tooltip_text("Logs for bug reports (build.log, environment, emerge --info) are in failure folder of build")
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

    def _update_build_row(self, running_build):
        """Only one build runs at a time (also of different projects), toolset can't be busy with other operation."""
        if running_build:
            # Plain row, like build progress row in project page.
            self.build_row.set_title("Show build progress")
            self.build_row.remove_css_class("suggested-action")
            self.build_row.add_css_class("regular-text")
            self.build_row.set_sensitive(True)
            self.build_row.set_tooltip_text(None)
            return
        self.build_row.remove_css_class("regular-text")
        self.build_row.add_css_class("suggested-action")
        toolset = self.project_directory.get_toolset()
        other_build = any(build.status == MultiStageProcessState.IN_PROGRESS
                          for build in MultiStageProcess.get_started_processes_by_class(ProjectBuild))
        reason = ("Another project is being built" if other_build
                  else toolset.busy_reason if toolset else "Select toolset in project configuration")
        self.build_row.set_title(GLib.markup_escape_text(reason) if reason else "Start new build")
        self.build_row.set_sensitive(reason is None)
        self.build_row.set_tooltip_text(reason)

    def _on_build_activated(self, row):
        if running_build := running_project_build(self.project_directory):
            self._open_build_progress(running_build)
            return
        project = self.project_directory
        if project.get_toolset() is None or project.get_releng_directory() is None or project.get_snapshot() is None:
            dialog = Adw.AlertDialog(heading="Can't start build", body="Please setup toolset, releng directory and snapshot first.")
            dialog.add_response("ok", "OK")
            dialog.present(self.get_root())
            return
        app_event_bus.emit(AppEvents.PRESENT_VIEW, ProjectBuildView(project_directory=project), "Build stages", 640, 480)

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

def _failure_summary(build) -> str | None:
    """Failed packages and reason. Builds made before details were recorded have them read from build.log."""
    if build is None:
        return None
    if "failure" not in build.details and build.path:
        try:
            with open(os.path.join(build.path, "build.log"), encoding="utf-8", errors="replace") as file:
                build.details["failure"] = failure_details(file.read().splitlines(), None)
        except OSError:
            return None
    return build.failure_summary
