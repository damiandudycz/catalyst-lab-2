from gi.repository import Gtk, Adw, GLib
from .git_directory import GitDirectoryEvent
from .project_manager import ProjectManager
from .project_directory import ProjectDirectory, ProjectConfiguration
from .toolset_application import ToolsetApplication
from .toolset import ToolsetEvents
from .repository import Repository
from .item_select_view import ItemSelectionViewEvent
import threading

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/project/project_details_view.ui')
class ProjectDetailsView(Gtk.Box):
    __gtype_name__ = "ProjectDetailsView"

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
        self.monitor_information_changes()
        self.monitor_configuration_changes()

    def get_configuration(self):
        if self.project_directory.metadata is None:
            return
        def get_by_id(items, target_id, attr):
            if not target_id:
                return None
            return next((item for item in items if getattr(item, attr) == target_id), None)
        metadata = self.project_directory.metadata
        toolset = get_by_id(Repository.Toolset.value, metadata.toolset_id, 'uuid')
        releng_directory = get_by_id(Repository.RelengDirectory.value, metadata.releng_directory_id, 'id')
        snapshot = get_by_id(Repository.Snapshot.value, metadata.snapshot_id, 'filename')
        self.toolset_selection_view.select(toolset)
        self.releng_selection_view.select(releng_directory)
        self.snapshot_selection_view.select(snapshot)

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
                for item in items:
                    item.event_bus.subscribe(
                        ToolsetEvents.IS_RESERVED_CHANGED,
                        self.toolset_selection_view.refresh_items_state
                    )
            case self.releng_selection_view:
                pass
            case self.snapshot_selection_view:
                pass

