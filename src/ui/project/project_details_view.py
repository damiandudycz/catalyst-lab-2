from gi.repository import Gtk, Adw, GLib
from .git_directory import GitDirectoryEvent
from .project_manager import ProjectManager
from .project_directory import ProjectDirectory, ProjectConfiguration
from .toolset_application import ToolsetApplication
from .toolset import ToolsetEvents
from .repository import Repository
from .item_select_view import ItemSelectionViewEvent
from .project_stage_create_view import ProjectStageCreateView
from .app_events import app_event_bus, AppEvents
from .stages_tree_view import StagesTreeView, TreeNode, StageNodeStatus
from .project_stage_details_view import ProjectStageDetailsView
from .project_build_view import ProjectBuildView
from .project_build_process import ProjectBuild, ProjectBuildStepBuildStage, running_project_build
from .project_builds_view import ProjectBuildsView
from .multistage_process import MultiStageProcess, MultiStageProcessState, MultiStageProcessEvent, MultiStageProcessStageEvent, MultiStageProcessStageState
from .architecture import Architecture
import threading

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/project/project_details_view.ui')
class ProjectDetailsView(Gtk.Box):
    __gtype_name__ = "ProjectDetailsView"

    stages_tree_view = Gtk.Template.Child()
    build_row = Gtk.Template.Child()
    build_progress_row = Gtk.Template.Child()
    directory_details_view = Gtk.Template.Child()
    toolset_selection_view = Gtk.Template.Child()
    releng_selection_view = Gtk.Template.Child()
    snapshot_selection_view = Gtk.Template.Child()
    arch_selection_view = Gtk.Template.Child()

    def __init__(self, project_directory: ProjectDirectory, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.project_directory = project_directory
        self.content_navigation_view = content_navigation_view
        self.apps_requirements = [ToolsetApplication.CATALYST]
        self.directory_details_view.setup(git_directory=project_directory, content_navigation_view=self.content_navigation_view)
        self.get_configuration()
        self.monitor_stages_changes()
        self.monitor_information_changes()
        self.monitor_configuration_changes()
        self.stages_tree_view.set_root_nodes(project_directory.stages_tree())
        self._observed_builds: set[int] = set() # Ids of builds whose changes are observed.
        self._build_refresh_scheduled = False
        MultiStageProcess.event_bus.subscribe(MultiStageProcessEvent.STARTED_PROCESSES_CHANGED, self._on_build_changed)
        self._refresh_build_state()

    def get_configuration(self):
        self.toolset_selection_view.select(self.project_directory.get_toolset())
        self.releng_selection_view.select(self.project_directory.get_releng_directory())
        self.snapshot_selection_view.select(self.project_directory.get_snapshot())
        self.arch_selection_view.select(self.project_directory.get_architecture())
        self.arch_selection_view.set_static_list(sorted(Architecture, key=lambda arch: arch.name))

    def configuration_item_changed(self, container):
        match container:
            case self.toolset_selection_view:
                self.project_directory.initialize_metadata().toolset_id = (
                    self.toolset_selection_view.selected_item.uuid
                    if self.toolset_selection_view.selected_item else None
                )
            case self.releng_selection_view:
                self.project_directory.initialize_metadata().releng_directory_id = (
                    self.releng_selection_view.selected_item.id
                    if self.releng_selection_view.selected_item else None
                )
            case self.snapshot_selection_view:
                self.project_directory.initialize_metadata().snapshot_id = (
                    self.snapshot_selection_view.selected_item.filename
                    if self.snapshot_selection_view.selected_item else None
                )
            case self.arch_selection_view:
                self.project_directory.initialize_metadata().architecture = (
                    self.arch_selection_view.selected_item
                    if self.arch_selection_view.selected_item else None
                )
        Repository.ProjectDirectory.save()

    def _update_name(self, name: str):
        self._page.set_title(name)

    def _update_stages(self, data):
        self.stages_tree_view.set_root_nodes(self.project_directory.stages_tree())

    # Running build
    # --------------------------------------------------------------------------

    def _refresh_build_state(self):
        """Shows states of stages in running build of this project and button to its progress."""
        running_builds = _running_builds()
        for build in running_builds:
            if id(build) not in self._observed_builds:
                self._observed_builds.add(id(build))
                build.event_bus.subscribe(MultiStageProcessEvent.STATE_CHANGED, self._on_build_changed)
                for step in build.stages:
                    step.event_bus.subscribe(MultiStageProcessStageEvent.STATE_CHANGED, self._on_build_changed)
                    step.event_bus.subscribe(MultiStageProcessStageEvent.PROGRESS_CHANGED, self._on_build_changed)
        running_build = running_project_build(self.project_directory)
        # Only one build can run at a time, also for different projects.
        self.build_row.set_visible(not running_builds)
        self.build_row.set_sensitive(not running_builds)
        self.build_progress_row.set_visible(running_build is not None)
        statuses = {}
        if running_build is not None:
            for step in running_build.stages:
                if isinstance(step, ProjectBuildStepBuildStage):
                    statuses[step.stage.id] = _build_step_status(step)
        self.stages_tree_view.set_statuses(statuses)

    def _on_build_changed(self, *args):
        # Several changes can come at once, refresh once.
        if self._build_refresh_scheduled:
            return
        self._build_refresh_scheduled = True
        def refresh():
            self._build_refresh_scheduled = False
            self._refresh_build_state()
            return False
        GLib.idle_add(refresh)

    def monitor_stages_changes(self):
        self.project_directory.event_bus.subscribe(
            GitDirectoryEvent.CONTENT_CHANGED,
            self._update_stages
        )

    def monitor_information_changes(self):
        self.project_directory.event_bus.subscribe(
            GitDirectoryEvent.NAME_CHANGED,
            self._update_name
        )

    def monitor_configuration_changes(self):
        """Reacts to changes in configuration lists, saves new metadata"""
        for view in [
            self.toolset_selection_view,
            self.releng_selection_view,
            self.snapshot_selection_view,
            self.arch_selection_view
        ]:
            view.event_bus.subscribe(
                ItemSelectionViewEvent.ITEM_CHANGED,
                self.configuration_item_changed
            )

    @Gtk.Template.Callback()
    def is_item_selectable(self, sender, item) -> bool:
        match sender:
            case self.toolset_selection_view:
                return all(item.get_app_install(app) is not None for app in self.apps_requirements)
            case self.releng_selection_view:
                return True
            case self.snapshot_selection_view:
                return True
            case self.arch_selection_view:
                return True
        return False

    @Gtk.Template.Callback()
    def is_item_usable(self, sender, item) -> bool:
        match sender:
            case self.toolset_selection_view:
                return True
            case self.releng_selection_view:
                return True
            case self.snapshot_selection_view:
                return True
            case self.arch_selection_view:
                return True
        return False

    @Gtk.Template.Callback()
    def setup_items_monitoring(self, sender, items):
        match sender:
            case self.toolset_selection_view:
                if hasattr(self, 'monitored_items_toolset'):
                    for item in self.monitored_items_toolset:
                        item.event_bus.unsubscribe(ToolsetEvents.IS_RESERVED_CHANGED, self)
                self.monitored_items_toolset = items
                for item in items:
                    item.event_bus.subscribe(
                        ToolsetEvents.IS_RESERVED_CHANGED,
                        self.toolset_selection_view.refresh_items_state,
                        self
                    )
            case self.releng_selection_view:
                pass
            case self.snapshot_selection_view:
                pass

    @Gtk.Template.Callback()
    def on_add_stage_activated(self, sender):
        if (
            self.project_directory.get_toolset() is None
            or self.project_directory.get_releng_directory() is None
            or self.project_directory.get_snapshot() is None
        ):
            print("Missing configuration")
            self.show_alert(message="Please setup toolset, releng directory and snapshot first.")
            return
        app_event_bus.emit(AppEvents.PRESENT_VIEW, ProjectStageCreateView(project_directory=self.project_directory), "New Stage", 640, 480)

    @Gtk.Template.Callback()
    def on_build_activated(self, sender):
        if (
            self.project_directory.get_toolset() is None
            or self.project_directory.get_releng_directory() is None
            or self.project_directory.get_snapshot() is None
        ):
            self.show_alert(message="Please setup toolset, releng directory and snapshot first.")
            return
        if _running_builds():
            self._refresh_build_state()
            return
        app_event_bus.emit(AppEvents.PRESENT_VIEW, ProjectBuildView(project_directory=self.project_directory), "Build stages", 640, 480)

    @Gtk.Template.Callback()
    def on_build_progress_activated(self, sender):
        if running_build := running_project_build(self.project_directory):
            app_event_bus.emit(AppEvents.PRESENT_VIEW, ProjectBuildView(project_directory=self.project_directory, installation_in_progress=running_build), "Build stages", 640, 480)
        else:
            self._refresh_build_state()

    @Gtk.Template.Callback()
    def on_builds_activated(self, sender):
        view = ProjectBuildsView(project_directory=self.project_directory, content_navigation_view=self.content_navigation_view)
        self.content_navigation_view.push_view(view, title=f"{self.project_directory.name} builds")

    @Gtk.Template.Callback()
    def on_stage_selected(self, sender, stage):
        if (
            self.project_directory.get_toolset() is None
            or self.project_directory.get_releng_directory() is None
            or self.project_directory.get_snapshot() is None
        ):
            print("Missing configuration")
            self.show_alert(message="Please setup toolset, releng directory and snapshot first.")
            return
        view = ProjectStageDetailsView(project_directory=self.project_directory, stage=stage, content_navigation_view=self.content_navigation_view)
        self.content_navigation_view.push_view(view, title=stage.name)

    def show_alert(self, message):
        dialog = Gtk.MessageDialog(
            transient_for=self.get_root(),
            modal=True,
            buttons=Gtk.ButtonsType.CLOSE,
            text=message
        )
        dialog.connect("response", lambda d, r: d.destroy())
        dialog.show()

def _running_builds() -> list[ProjectBuild]:
    """Builds in progress, of all projects."""
    return [
        build for build in MultiStageProcess.get_started_processes_by_class(ProjectBuild)
        if build.status == MultiStageProcessState.IN_PROGRESS
    ]

def _build_step_status(step: ProjectBuildStepBuildStage) -> StageNodeStatus:
    match step.state:
        case MultiStageProcessStageState.IN_PROGRESS:
            # Progress of built packages.
            return StageNodeStatus(title=f"Building {int(step.progress * 100)}%" if step.progress is not None else "Building", in_progress=True)
        case MultiStageProcessStageState.COMPLETED:
            return StageNodeStatus(title="Built", icon_name="check-square-svgrepo-com-symbolic", css_class="success")
        case MultiStageProcessStageState.FAILED if step.build is None:
            # Failed without starting, because stage it depends on failed.
            return StageNodeStatus(title="Skipped", icon_name="square-svgrepo-com-symbolic", css_class="dimmed")
        case MultiStageProcessStageState.FAILED:
            return StageNodeStatus(title="Failed", icon_name="error-box-svgrepo-com-symbolic", css_class="error")
    return StageNodeStatus(title="Scheduled", icon_name="clock-square-svgrepo-com-symbolic", css_class="dimmed")
