import os
from datetime import datetime
from gi.repository import Gtk, Adw, GLib
from .app_section import app_section
from .app_events import app_event_bus, AppEvents
from .deploy_create_view import DeployCreateView
from .deploy_installation import DeployInstallation, deployable_builds
from .deploy_target import format_size
from .project_stage import stage_target_icon
from .multistage_process import MultiStageProcess, MultiStageProcessEvent, MultiStageProcessState

@app_section(title="Deploy", icon="deploy-symbolic", order=7_000)
class DeploySection(Gtk.Box):
    """Builds of stage3 and stage4 that can be installed on another machine, and deployments started in this session.
    Selecting build opens deploy wizard."""

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self.content_navigation_view = content_navigation_view
        self.page = Adw.PreferencesPage(vexpand=True)
        self.append(self.page)
        self.groups: list[Adw.PreferencesGroup] = []
        # Builds can finish while app runs, list is refreshed when shown.
        self.connect("map", lambda widget: self.load())
        MultiStageProcess.event_bus.subscribe(MultiStageProcessEvent.STARTED_PROCESSES_CHANGED, lambda *args: GLib.idle_add(self.load))

    def load(self):
        for group in self.groups:
            self.page.remove(group)
        self.groups = []
        # Deployments
        deployments = [process for process in MultiStageProcess.started_processes if isinstance(process, DeployInstallation)]
        if deployments:
            group = Adw.PreferencesGroup(title="Deployments")
            for deployment in reversed(deployments):
                row = Adw.ActionRow(title=GLib.markup_escape_text(deployment.name()), subtitle=_deployment_state(deployment),
                                    icon_name="deploy-symbolic", activatable=True)
                row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
                row.connect("activated", self._on_deployment_activated, deployment)
                group.add(row)
            self._add_group(group)
        # Builds
        projects = deployable_builds()
        if not projects:
            group = Adw.PreferencesGroup()
            group.add(Adw.StatusPage(
                icon_name="deploy-symbolic",
                title="No builds to deploy",
                description="Stage3 and stage4 builds can be installed on another machine booted from Gentoo LiveCD. Build them in a project first."
            ))
            self._add_group(group)
            return
        for project, builds in projects:
            group = Adw.PreferencesGroup(title=GLib.markup_escape_text(project.name),
                                         description="Select build to install it on another machine")
            stages = {stage.id: stage for stage in project.stages}
            for build in builds:
                stage = stages.get(build.stage_id)
                size = os.path.getsize(build.artifact_path)
                row = Adw.ActionRow(
                    title=GLib.markup_escape_text(build.stage_name),
                    subtitle=GLib.markup_escape_text(f"{build.date.strftime('%Y-%m-%d %H:%M')} · {build.artifact} · {format_size(size)}"),
                    icon_name=stage_target_icon(stage.target if stage else None),
                    activatable=True,
                )
                row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
                row.connect("activated", self._on_build_activated, project, build)
                group.add(row)
            self._add_group(group)
        return False

    def _add_group(self, group: Adw.PreferencesGroup):
        self.page.add(group)
        self.groups.append(group)

    def _on_build_activated(self, row, project, build):
        app_event_bus.emit(AppEvents.PRESENT_VIEW, DeployCreateView(project_directory=project, build=build), "Deploy build", 640, 560)

    def _on_deployment_activated(self, row, deployment):
        app_event_bus.emit(AppEvents.PRESENT_VIEW, DeployCreateView(installation_in_progress=deployment), "Deploy build", 640, 560)

def _deployment_state(deployment: DeployInstallation) -> str:
    return {
        MultiStageProcessState.IN_PROGRESS: f"In progress, {int(deployment.progress * 100)}%",
        MultiStageProcessState.COMPLETED: "Completed",
        MultiStageProcessState.FAILED: "Failed",
    }.get(deployment.status, "Starting")
