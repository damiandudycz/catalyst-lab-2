from __future__ import annotations
import os
from gi.repository import Gtk, Adw, GLib, Gio
from .project_directory import ProjectDirectory
from .project_build import load_project_builds
from .project_stage import stage_target_icon
from .deploy_installation import is_deployable
from .deploy_live_iso import is_live_iso_build, supports_raspberry_pi_iso, raspberry_pi_iso_path, RaspberryPiIsoCreation
from .multistage_process import MultiStageProcess, MultiStageProcessState, MultiStageProcessEvent
from .deploy_create_view import DeployCreateView
from .app_events import app_event_bus, AppEvents
from .helper_functions import get_file_size_string

class ProjectDeployView(Gtk.Box):
    """Builds of project that can be deployed (stage3, stage4), newest first. Selecting build opens deploy wizard.
    Live CD builds (ISO images) are listed below them: ISO with Raspberry Pi kernel can be made bootable by Raspberry Pi
    firmware, other ones are written to USB drives as they are. Shown from Deploy section, like project builds are
    shown from Builds section."""

    def __init__(self, project_directory: ProjectDirectory, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.project_directory = project_directory
        self.content_navigation_view = content_navigation_view
        # Same layout as other views: groups use whole width of window.
        scrolled_window = Gtk.ScrolledWindow(hexpand=True, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_start=24, margin_end=24, margin_top=6, margin_bottom=24)
        scrolled_window.set_child(self.content)
        self.append(scrolled_window)
        self.groups: list[Adw.PreferencesGroup] = []
        # Builds can finish or be removed while view is open, refresh when shown again.
        self.connect("map", lambda widget: self.load_builds())

    def load_builds(self):
        for group in self.groups:
            self.content.remove(group)
        self.groups = []
        all_builds = load_project_builds(self.project_directory)
        builds = [build for build in all_builds if is_deployable(self.project_directory, build)]
        iso_builds = [build for build in all_builds if is_live_iso_build(build)]
        if not builds and not iso_builds:
            group = Adw.PreferencesGroup()
            group.add(Adw.StatusPage(
                icon_name="deploy-symbolic",
                title="No builds to deploy",
                description="Build stage3, stage4 or live CD of this project from its Stages page."
            ))
            self._add_group(group)
            return
        stages = {stage.id: stage for stage in self.project_directory.stages}
        if not builds:
            self._add_live_cd_group(iso_builds, stages)
            return
        group = Adw.PreferencesGroup(title="Builds", description="Select build to install it on another machine or disk")
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
        if iso_builds:
            self._add_live_cd_group(iso_builds, stages)

    def _add_live_cd_group(self, builds: list, stages: dict):
        group = Adw.PreferencesGroup(title="Live CD", description="ISO images are written to USB drive or SD card as they "
                                     "are (dd, Raspberry Pi Imager). Raspberry Pi doesn't boot them, ISO with boot "
                                     "partition for it is made from them.")
        for build in builds:
            stage = stages.get(build.stage_id)
            details = [build.date.strftime("%Y-%m-%d %H:%M"), build.artifact]
            if size := get_file_size_string(build.artifact_path):
                details.append(size)
            raspberry_pi = supports_raspberry_pi_iso(self.project_directory, build)
            if raspberry_pi and os.path.isfile(raspberry_pi_iso_path(build)):
                details.append(f"Raspberry Pi: {os.path.basename(raspberry_pi_iso_path(build))}")
            row = Adw.ActionRow(title=GLib.markup_escape_text(build.stage_name), subtitle=GLib.markup_escape_text(" · ".join(details)))
            row.add_prefix(Gtk.Image.new_from_icon_name(stage_target_icon(stage.target if stage else None)))
            if raspberry_pi:
                running = self._running_raspberry_pi_iso(build)
                button = Gtk.Button(label="Creating Raspberry Pi ISO" if running else "Create Raspberry Pi ISO",
                                    valign=Gtk.Align.CENTER, sensitive=running is None,
                                    tooltip_text="ISO with FAT boot partition read by Raspberry Pi firmware: kernel of "
                                                 "ISO, device trees of its release, config.txt and cmdline.txt")
                button.connect("clicked", lambda _, build=build: self._confirm_raspberry_pi_iso(build))
                row.add_suffix(button)
            folder = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Open build folder", valign=Gtk.Align.CENTER)
            folder.add_css_class("flat")
            folder.connect("clicked", lambda _, path=build.path: Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(self.get_root(), None, None))
            row.add_suffix(folder)
            group.add(row)
        self._add_group(group)

    @staticmethod
    def _running_raspberry_pi_iso(build) -> RaspberryPiIsoCreation | None:
        return next((process for process in MultiStageProcess.get_started_processes_by_class(RaspberryPiIsoCreation)
                     if process.build.id == build.id and process.status == MultiStageProcessState.IN_PROGRESS), None)

    def _confirm_raspberry_pi_iso(self, build):
        path = raspberry_pi_iso_path(build)
        replaced = " It replaces ISO created before." if os.path.isfile(path) else ""
        dialog = Adw.AlertDialog(heading="Create Raspberry Pi ISO?",
                                 body=f"{os.path.basename(path)} will be created next to {build.artifact}.{replaced} Device "
                                      f"trees of kernel release are downloaded from Raspberry Pi firmware repository "
                                      f"the first time.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _, response: self._create_raspberry_pi_iso(build) if response == "create" else None)
        dialog.present(self.get_root())

    def _create_raspberry_pi_iso(self, build):
        from .multistage_process_execution_view import MultistageProcessExecutionView
        process = RaspberryPiIsoCreation(self.project_directory, build)
        process.start()
        view = MultistageProcessExecutionView()
        view.set_multistage_process(multistage_process=process)
        app_event_bus.emit(AppEvents.PRESENT_VIEW, view, "Raspberry Pi ISO", 640, 480)
        process.event_bus.subscribe(MultiStageProcessEvent.STATE_CHANGED, self._on_raspberry_pi_iso_state, self)
        self.load_builds()

    def _on_raspberry_pi_iso_state(self, *args):
        GLib.idle_add(lambda: self.load_builds() and False)

    def _add_group(self, group: Adw.PreferencesGroup):
        self.content.append(group)
        self.groups.append(group)
