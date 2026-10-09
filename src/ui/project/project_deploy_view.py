from __future__ import annotations
import os
from gi.repository import Gtk, Adw, GLib
from .project_directory import ProjectDirectory
from .project_build import load_project_builds
from .project_stage import stage_target_icon
from .deploy_installation import is_deployable
from .deploy_create_view import DeployCreateView
from .app_events import app_event_bus, AppEvents
from .helper_functions import get_file_size_string

class ProjectDeployView(Gtk.Box):
    """Builds of project that can be deployed (stage3, stage4), newest first. Selecting build opens deploy wizard.
    Shown from Deploy section, like project builds are shown from Builds section."""

    def __init__(self, project_directory: ProjectDirectory, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.project_directory = project_directory
        self.content_navigation_view = content_navigation_view
        self.page = Adw.PreferencesPage(vexpand=True)
        self.append(self.page)
        self.groups: list[Adw.PreferencesGroup] = []
        # Builds can finish or be removed while view is open, refresh when shown again.
        self.connect("map", lambda widget: self.load_builds())

    def load_builds(self):
        for group in self.groups:
            self.page.remove(group)
        self.groups = []
        builds = [build for build in load_project_builds(self.project_directory) if is_deployable(self.project_directory, build)]
        if not builds:
            group = Adw.PreferencesGroup()
            group.add(Adw.StatusPage(
                icon_name="deploy-symbolic",
                title="No builds to deploy",
                description="Build stage3 or stage4 of this project from its Stages page."
            ))
            self._add_group(group)
            return
        stages = {stage.id: stage for stage in self.project_directory.stages}
        group = Adw.PreferencesGroup(title="Builds", description="Select build to install it on another machine")
        for build in builds:
            stage = stages.get(build.stage_id)
            details = [build.date.strftime("%Y-%m-%d %H:%M"), build.artifact]
            if size := get_file_size_string(build.artifact_path):
                details.append(size)
            if stage is None:
                details.append("stage was removed from project")
            row = Adw.ActionRow(title=GLib.markup_escape_text(build.stage_name), subtitle=GLib.markup_escape_text(" · ".join(details)),
                                activatable=True)
            icon = Gtk.Image.new_from_icon_name(stage_target_icon(stage.target if stage else None))
            row.add_prefix(icon)
            arrow = Gtk.Image.new_from_icon_name("go-next-symbolic")
            arrow.add_css_class("dimmed")
            row.add_suffix(arrow)
            row.connect("activated", lambda _, build=build: app_event_bus.emit(
                AppEvents.PRESENT_VIEW, DeployCreateView(project_directory=self.project_directory, build=build), "Deploy build", 640, 560))
            group.add(row)
        self._add_group(group)

    def _add_group(self, group: Adw.PreferencesGroup):
        self.page.add(group)
        self.groups.append(group)
