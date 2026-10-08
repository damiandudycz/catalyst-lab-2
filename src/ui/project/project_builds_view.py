from __future__ import annotations
import os
from gi.repository import Gtk, Adw, GLib, Gio
from .project_directory import ProjectDirectory
from .project_build import StageBuildStatus, load_project_builds
from .helper_functions import get_file_size_string

class ProjectBuildsView(Gtk.Box):
    """Lists builds of project stages, grouped by stage in tree order. Shown from Builds section."""

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
        # Groups for stages in tree order, then builds of stages that were removed from project.
        stage_ids = set()
        for stage in self._stages_in_tree_order():
            stage_ids.add(stage.id)
            self._add_stage_group(title=stage.name, builds=[build for build in builds if build.stage_id == stage.id])
        removed = [build for build in builds if build.stage_id not in stage_ids]
        for stage_name in dict.fromkeys(build.stage_name for build in removed):
            self._add_stage_group(
                title=stage_name, description="Stage was removed from project",
                builds=[build for build in removed if build.stage_name == stage_name]
            )

    def _stages_in_tree_order(self) -> list:
        result = []
        def visit(node):
            result.append(node.value)
            for child in node.children:
                visit(child)
        for root in self.project_directory.stages_tree():
            visit(root)
        return result

    def _add_group(self, group: Adw.PreferencesGroup):
        self.page.add(group)
        self.groups.append(group)

    def _add_stage_group(self, title: str, builds: list, description: str | None = None):
        if not builds:
            return
        group = Adw.PreferencesGroup(title=GLib.markup_escape_text(title))
        if description:
            group.set_description(description)
        for build in builds:
            group.add(self._build_row(build))
        self._add_group(group)

    def _build_row(self, build) -> Adw.ActionRow:
        details = [_status_name(build.status)]
        if build.artifact_path and os.path.isfile(build.artifact_path):
            details.append(build.artifact)
            if size := get_file_size_string(build.artifact_path):
                details.append(size)
        row = Adw.ActionRow(
            title=build.date.strftime("%Y-%m-%d %H:%M"),
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
