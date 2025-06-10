from __future__ import annotations
from gi.repository import Gtk, Adw
from .multistage_process import MultiStageProcessState
from .project_installation import ProjectInstallation
from .git_directory_create_config_view import GitDirectoryCreateConfigViewEvent
from .default_dir_content_builder import DefaultDirContentBuilder
from .git_installation import GitDirectorySetupConfiguration
from .toolset_select_view import ToolsetSelectionViewEvent
import os

class DefaultProjectDirContentBuilder(DefaultDirContentBuilder):
    def build_in(self, path: str, repo_name: str):
        pass

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/project_create/project_create_view.ui')
class ProjectCreateView(Gtk.Box):
    __gtype_name__ = "ProjectCreateView"

    # Main views:
    setup_view = Gtk.Template.Child()
    install_view = Gtk.Template.Child()
    # Setup view elements:
    carousel = Gtk.Template.Child()
    config_page = Gtk.Template.Child()
    toolset_selection_view = Gtk.Template.Child()
    back_button = Gtk.Template.Child()
    next_button = Gtk.Template.Child()

    def __init__(self, installation_in_progress: ProjectInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.current_page = 0
        self.carousel.connect('page-changed', self.on_page_changed)
        self._set_current_stage(self.installation_in_progress.status if self.installation_in_progress else MultiStageProcessState.SETUP)
        self.install_view.set_multistage_process(self.installation_in_progress)
        self.toolset_selection_view.event_bus.subscribe(
            ToolsetSelectionViewEvent.TOOLSET_CHANGED,
            self.setup_back_next_buttons
        )
        self.connect("realize", self.on_realize)

    def on_realize(self, widget):
        self.install_view.content_navigation_view = self.content_navigation_view
        self.install_view._window = self._window
        self.config_page.event_bus.subscribe(
            GitDirectoryCreateConfigViewEvent.CONFIGURATION_READY_CHANGED,
            self.setup_back_next_buttons,
            self
        )

    def on_page_changed(self, carousel, pspec):
        self.current_page = int(carousel.get_position())
        self.setup_back_next_buttons()

    def setup_back_next_buttons(self, _ = None):
        is_first_page = self.current_page == 0
        is_second_page = self.current_page == 1
        is_last_page = self.current_page == 2
        self.back_button.set_sensitive(not is_first_page)
        self.back_button.set_opacity(0.0 if is_first_page else 1.0)
        self.next_button.set_sensitive(
            is_second_page and self.config_page.configuration_ready
            or is_last_page and self.config_page.configuration_ready and not (self.toolset_selection_view.selected_toolset is None or self.toolset_selection_view.selected_toolset.is_reserved)
        )
        self.next_button.set_opacity(0.0 if is_first_page else 1.0)
        self.next_button.set_label("Create project" if is_last_page else "Next")

    @Gtk.Template.Callback()
    def on_back_pressed(self, _):
        is_first_page = self.current_page == 0
        if not is_first_page:
            self.carousel.scroll_to(self.carousel.get_nth_page(self.current_page - 1), True)

    @Gtk.Template.Callback()
    def on_next_pressed(self, _):
        is_last_page = self.current_page == 2
        if not is_last_page:
            self.carousel.scroll_to(self.carousel.get_nth_page(self.current_page + 1), True)
        else:
            configuration = self.config_page.get_configuration(default_dir_content_builder=DefaultProjectDirContentBuilder())
            self._start_installation(configuration=configuration)

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        self.carousel.scroll_to(self.config_page, True)

    def _set_current_stage(self, stage: MultiStageProcessState):
        # Setup views visibility:
        self.setup_view.set_visible(stage == MultiStageProcessState.SETUP)
        self.install_view.set_visible(stage != MultiStageProcessState.SETUP)

    def _start_installation(self, configuration: GitDirectorySetupConfiguration):
        self.installation_in_progress = ProjectInstallation(configuration=configuration)
        self.installation_in_progress.start()
        self.install_view.set_multistage_process(self.installation_in_progress)
        self._set_current_stage(self.installation_in_progress.status)

