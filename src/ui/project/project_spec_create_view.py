from __future__ import annotations
from gi.repository import Gtk, Adw, GLib
from .multistage_process import MultiStageProcessState
from .project_installation import ProjectInstallation
from .git_directory_create_config_view import GitDirectoryCreateConfigViewEvent
from .git_directory_default_content_builder import DefaultDirContentBuilder
from .git_installation import GitDirectorySetupConfiguration
from .toolset_application import ToolsetApplication
from .toolset import ToolsetEvents
from .wizard_view import WizardView
from .item_select_view import ItemSelectionViewEvent
from .toolset import Toolset
from .releng_directory import RelengDirectory
from .snapshot import Snapshot
from .project_spec import load_catalyst_targets
import os, threading

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/project/project_spec_create_view.ui')
class ProjectSpecCreateView(Gtk.Box):
    __gtype_name__ = "ProjectSpecCreateView"

    # Main views:
    wizard_view = Gtk.Template.Child()
    # Setup view elements:
    spec_type_page = Gtk.Template.Child()
    spec_type_selection_view = Gtk.Template.Child()

    def __init__(self, project_directory: ProjectDirectory, installation_in_progress: ProjectInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.project_directory = project_directory
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.load_targets()
        self.spec_type_selection_view.event_bus.subscribe(
            ItemSelectionViewEvent.ITEM_CHANGED,
            self.spec_type_changed
        )
        self.connect("realize", self.on_realize)

    def spec_type_changed(self, data):
        self.wizard_view._refresh_buttons_state()

    def on_realize(self, widget):
        self.wizard_view.content_navigation_view = self.content_navigation_view
        self.wizard_view._window = self._window
        self.wizard_view.set_installation(self.installation_in_progress)

    def load_targets(self):
        class SpecTargetContainer:
            """Helper class used to display elements in item_select_view."""
            def __init__(self, target_name: str):
                self.target_name = target_name
            @property
            def name(self) -> str:
                return self.target_name
        def worker():
            toolset = self.project_directory.get_toolset()
            target_names = load_catalyst_targets(toolset=toolset)
            targets = [SpecTargetContainer(target_name=name) for name in target_names]
            GLib.idle_add(self.spec_type_selection_view.set_static_list, targets)
        threading.Thread(target=worker, daemon=True).start()

    @Gtk.Template.Callback()
    def is_page_ready_to_continue(self, sender, page) -> bool:
        match page:
            case self.spec_type_page:
                return self.spec_type_selection_view.selected_item is not None
        return True

    @Gtk.Template.Callback()
    def is_item_selectable(self, sender, item) -> bool:
        match sender:
            case self.spec_type_selection_view:
                return True
        return False

    @Gtk.Template.Callback()
    def is_item_usable(self, sender, item) -> bool:
        match sender:
            case self.spec_type_selection_view:
                return True
        return False

    @Gtk.Template.Callback()
    def begin_installation(self, view):
        self._start_installation(
        )

    def _start_installation(
        self,
    ):
        # ...
        self.wizard_view.set_installation(installation_in_progress)

