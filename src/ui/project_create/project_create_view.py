from __future__ import annotations
from gi.repository import Gtk, Adw
from .multistage_process import MultiStageProcessState
from .project_installation import ProjectInstallation
from .git_directory_create_config_view import GitDirectoryCreateConfigViewEvent
from .default_dir_content_builder import DefaultDirContentBuilder
from .git_installation import GitDirectorySetupConfiguration
from .toolset_select_view import ToolsetSelectionViewEvent
from .releng_select_view import RelengSelectionViewEvent
from .snapshot_select_view import SnapshotSelectionViewEvent
from .wizard_view import WizardView
import os

class DefaultProjectDirContentBuilder(DefaultDirContentBuilder):
    def build_in(self, path: str, repo_name: str):
        pass

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/project_create/project_create_view.ui')
class ProjectCreateView(Gtk.Box):
    __gtype_name__ = "ProjectCreateView"

    # Main views:
    wizard_view = Gtk.Template.Child()
    # Setup view elements:
    config_page = Gtk.Template.Child()
    toolset_page = Gtk.Template.Child()
    releng_page = Gtk.Template.Child()
    snapshot_page = Gtk.Template.Child()
    toolset_selection_view = Gtk.Template.Child()
    releng_selection_view = Gtk.Template.Child()
    snapshot_selection_view = Gtk.Template.Child()

    def __init__(self, installation_in_progress: ProjectInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.config_page.event_bus.subscribe(
            GitDirectoryCreateConfigViewEvent.CONFIGURATION_READY_CHANGED,
            self.config_ready_changed
        )
        self.toolset_selection_view.event_bus.subscribe(
            ToolsetSelectionViewEvent.TOOLSET_CHANGED,
            self.toolset_changed
        )
        self.releng_selection_view.event_bus.subscribe(
            RelengSelectionViewEvent.SELECTION_CHANGED,
            self.releng_changed
        )
        self.snapshot_selection_view.event_bus.subscribe(
            SnapshotSelectionViewEvent.SELECTION_CHANGED,
            self.snapshot_changed
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

    def on_realize(self, widget):
        self.wizard_view.content_navigation_view = self.content_navigation_view
        self.wizard_view._window = self._window
        self.wizard_view.set_installation(self.installation_in_progress)

    @Gtk.Template.Callback()
    def is_page_ready_to_continue(self, sender, page) -> bool:
        match page:
            case self.config_page:
                return self.config_page.configuration_ready
            case self.toolset_page:
                return not (self.toolset_selection_view.selected_toolset is None or self.toolset_selection_view.selected_toolset.is_reserved)
            case self.releng_page:
                return self.releng_selection_view.selected_releng_directory is not None
            case self.snapshot_page:
                return self.snapshot_selection_view.selected_snapshot is not None
        return True

    @Gtk.Template.Callback()
    def begin_installation(self, view):
        self._start_installation(configuration=self.config_page.get_configuration(default_dir_content_builder=DefaultProjectDirContentBuilder()))

    def _start_installation(self, configuration: GitDirectorySetupConfiguration):
        installation_in_progress = ProjectInstallation(configuration=configuration)
        installation_in_progress.start()
        self.wizard_view.set_installation(installation_in_progress)

