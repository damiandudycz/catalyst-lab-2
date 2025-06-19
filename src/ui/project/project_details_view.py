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
from .stages_tree_view import StagesTreeView, TreeNode
from .project_stage_details_view import ProjectStageDetailsView
import threading

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/project/project_details_view.ui')
class ProjectDetailsView(Gtk.Box):
    __gtype_name__ = "ProjectDetailsView"

    stages_tree_view = Gtk.Template.Child()
    directory_details_view = Gtk.Template.Child()
    toolset_selection_view = Gtk.Template.Child()
    releng_selection_view = Gtk.Template.Child()
    snapshot_selection_view = Gtk.Template.Child()

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

    def get_configuration(self):
        self.toolset_selection_view.select(self.project_directory.get_toolset())
        self.releng_selection_view.select(self.project_directory.get_releng_directory())
        self.snapshot_selection_view.select(self.project_directory.get_snapshot())

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
        Repository.ProjectDirectory.save()

    def _update_name(self, name: str):
        self._page.set_title(name)

    def _update_stages(self, data):
        self.stages_tree_view.set_root_nodes(self.project_directory.stages_tree())

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
            self.snapshot_selection_view
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
    def on_stage_selected(self, sender, stage):
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

