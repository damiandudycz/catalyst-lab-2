from __future__ import annotations
from gi.repository import Gtk, Adw
from .multistage_process import MultiStageProcessState
from .project_installation import ProjectInstallation
from .git_directory_create_config_view import GitDirectoryCreateConfigViewEvent
from .git_directory_default_content_builder import DefaultDirContentBuilder
from .git_installation import GitDirectorySetupConfiguration
from .toolset_application import ToolsetApplication
from .toolset import ToolsetEvents
from .wizard_view import WizardView
from .item_select_view import ItemSelectionViewEvent
from .architecture import Architecture
import os

class DefaultProjectDirContentBuilder(DefaultDirContentBuilder):
    def build_in(self, path: str, repo_name: str):
        structure = ['stages']
        for folder in structure:
            os.makedirs(os.path.join(path, folder), exist_ok=True)

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/project/project_create_view.ui')
class ProjectCreateView(Gtk.Box):
    __gtype_name__ = "ProjectCreateView"

    # Main views:
    wizard_view = Gtk.Template.Child()
    # Setup view elements:
    source_page = Gtk.Template.Child()
    source_view = Gtk.Template.Child()
    toolset_page = Gtk.Template.Child()
    toolset_selection_view = Gtk.Template.Child()
    releng_page = Gtk.Template.Child()
    releng_selection_view = Gtk.Template.Child()
    snapshot_page = Gtk.Template.Child()
    snapshot_selection_view = Gtk.Template.Child()
    arch_page = Gtk.Template.Child()
    arch_selection_view = Gtk.Template.Child()

    def __init__(self, installation_in_progress: ProjectInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.apps_requirements = [ToolsetApplication.CATALYST]
        self.arch_selection_view.set_static_list(sorted(Architecture, key=lambda arch: arch.name))
        self.source_view.event_bus.subscribe(
            GitDirectoryCreateConfigViewEvent.CONFIGURATION_READY_CHANGED,
            self.config_ready_changed
        )
        self.toolset_selection_view.event_bus.subscribe(
            ItemSelectionViewEvent.ITEM_CHANGED,
            self.toolset_changed
        )
        self.releng_selection_view.event_bus.subscribe(
            ItemSelectionViewEvent.ITEM_CHANGED,
            self.releng_changed
        )
        self.snapshot_selection_view.event_bus.subscribe(
            ItemSelectionViewEvent.ITEM_CHANGED,
            self.snapshot_changed
        )
        self.arch_selection_view.event_bus.subscribe(
            ItemSelectionViewEvent.ITEM_CHANGED,
            self.arch_changed
        )
        self.connect("realize", self.on_realize)

    def config_ready_changed(self, data):
        self.wizard_view._refresh_buttons_state()

    def toolset_changed(self, data):
        self.wizard_view._refresh_buttons_state()

    def releng_changed(self, data):
        self.wizard_view._refresh_buttons_state()

    def snapshot_changed(self, data):
        self.wizard_view._refresh_buttons_state()

    def arch_changed(self, data):
        self.wizard_view._refresh_buttons_state()

    def on_realize(self, widget):
        self.wizard_view.content_navigation_view = self.content_navigation_view
        self.wizard_view._window = self._window
        self.wizard_view.set_installation(self.installation_in_progress)

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
                return not item.is_reserved
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
            case self.arch_selection_view:
                pass

    @Gtk.Template.Callback()
    def is_page_ready_to_continue(self, sender, page) -> bool:
        match page:
            case self.source_page:
                return self.source_view.configuration_ready
            case self.toolset_page:
                return (
                    self.toolset_selection_view.selected_item is not None
                    and self.is_item_usable(self.toolset_selection_view, self.toolset_selection_view.selected_item)
                    and self.is_item_selectable(self.toolset_selection_view, self.toolset_selection_view.selected_item)
                )
            case self.releng_page:
                return self.releng_selection_view.selected_item is not None
            case self.snapshot_page:
                return self.snapshot_selection_view.selected_item is not None
            case self.arch_page:
                return self.arch_selection_view.selected_item is not None
        return True

    @Gtk.Template.Callback()
    def begin_installation(self, view):
        self._start_installation(
            source_config=self.source_view.get_configuration(default_dir_content_builder=DefaultProjectDirContentBuilder()),
            toolset=self.toolset_selection_view.selected_item,
            releng_directory=self.releng_selection_view.selected_item,
            snapshot=self.snapshot_selection_view.selected_item,
            architecture=self.arch_selection_view.selected_item
        )

    def _start_installation(
        self,
        source_config: GitDirectorySetupConfiguration,
        toolset: Toolset,
        releng_directory: RelengDirectory,
        snapshot: Snapshot,
        architecture: Architecture
    ):
        installation_in_progress = ProjectInstallation(
            source_config=source_config,
            toolset=toolset,
            releng_directory=releng_directory,
            snapshot=snapshot,
            architecture=architecture
        )
        installation_in_progress.start()
        self.wizard_view.set_installation(installation_in_progress)

