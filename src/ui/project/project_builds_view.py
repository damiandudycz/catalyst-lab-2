from __future__ import annotations
import os
from datetime import datetime, timezone
from gi.repository import Gtk, Adw, GLib, Gio
from .project_directory import ProjectDirectory
from .project_build import StageBuildStatus, load_project_builds
from .helper_functions import get_file_size_string

class ProjectBuildsView(Gtk.Box):
    """Lists builds of project stages, grouped by build runs (stages built together). Shown from Builds section."""

    def __init__(self, project_directory: ProjectDirectory, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.project_directory = project_directory
        self.content_navigation_view = content_navigation_view
        self.page = Adw.PreferencesPage()
        self.page.set_vexpand(True)
        self.append(self.page)
        self.groups: list[Adw.PreferencesGroup] = []
        # Builds can change while view is open (build finished), refresh when shown again.
        self.connect("map", lambda widget: self.load_builds())

    def load_builds(self):
        for group in self.groups:
            self.page.remove(group)
        self.groups = []
        builds = load_project_builds(self.project_directory)
        if not builds:
            group = Adw.PreferencesGroup()
            group.add(Adw.StatusPage(
                icon_name="box-minimalistic-svgrepo-com-symbolic",
                title="No builds yet",
                description="Build stages of this project from its Stages page."
            ))
            self._add_group(group)
            return
        # Stages built together share timestamp, group them as one build run. Newest runs first (builds are loaded
        # newest first), stages in order they were built.
        stage_ids = {stage.id for stage in self.project_directory.stages}
        runs: dict[str, list] = {}
        for build in builds:
            runs.setdefault(build.timestamp, []).append(build)
        for timestamp, run_builds in runs.items():
            run_builds.sort(key=lambda build: build.date)
            group = Adw.PreferencesGroup(
                title=f"Started {_format_timestamp(timestamp, fallback=run_builds[0].date)}",
                description=_run_summary(run_builds)
            )
            for build in run_builds:
                group.add(self._build_row(build, stage_removed=build.stage_id not in stage_ids))
            self._add_group(group)

    def _add_group(self, group: Adw.PreferencesGroup):
        self.page.add(group)
        self.groups.append(group)

    def _build_row(self, build, stage_removed: bool) -> Adw.ActionRow:
        details = [_status_name(build.status)]
        if stage_removed:
            details.append("stage was removed from project")
        if build.artifact_path and os.path.isfile(build.artifact_path):
            details.append(build.artifact)
            if size := get_file_size_string(build.artifact_path):
                details.append(size)
        row = Adw.ActionRow(
            title=GLib.markup_escape_text(build.stage_name),
            subtitle=GLib.markup_escape_text(" · ".join(details))
        )
        icon = Gtk.Image.new_from_icon_name(_status_icon(build.status))
        icon.set_pixel_size(24)
        if css_class := _status_css_class(build.status):
            icon.add_css_class(css_class)
        row.add_prefix(icon)
        if build.path and os.path.isdir(build.path):
            button = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Open build folder", valign=Gtk.Align.CENTER)
            button.add_css_class("flat")
            button.connect("clicked", lambda _, path=build.path: Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(self.get_root(), None, None))
            row.add_suffix(button)
        return row

def _format_timestamp(timestamp: str, fallback: datetime) -> str:
    """Build run timestamp (@TIMESTAMP@, in UTC) in local time."""
    try:
        date = datetime.strptime(timestamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone()
    except ValueError:
        date = fallback
    return date.strftime("%Y-%m-%d %H:%M")

def _run_summary(builds: list) -> str:
    parts = [f"{len(builds)} stage{'s' if len(builds) != 1 else ''}"]
    for status in (StageBuildStatus.COMPLETED, StageBuildStatus.FAILED, StageBuildStatus.IN_PROGRESS):
        if count := sum(1 for build in builds if build.status == status):
            parts.append(f"{count} {_status_name(status).lower()}")
    return ", ".join(parts)

def _status_name(status: StageBuildStatus) -> str:
    match status:
        case StageBuildStatus.COMPLETED: return "Completed"
        case StageBuildStatus.FAILED: return "Failed"
        case _: return "Not finished"

def _status_icon(status: StageBuildStatus) -> str:
    match status:
        case StageBuildStatus.COMPLETED: return "check-square-svgrepo-com-symbolic"
        case StageBuildStatus.FAILED: return "error-box-svgrepo-com-symbolic"
        case _: return "menu-dots-square-svgrepo-com-symbolic"

def _status_css_class(status: StageBuildStatus) -> str | None:
    match status:
        case StageBuildStatus.COMPLETED: return "success"
        case StageBuildStatus.FAILED: return "error"
        case _: return None
