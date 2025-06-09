from __future__ import annotations
from gi.repository import Gtk, Adw
from .multistage_process import MultiStageProcessState
from .overlay_installation import OverlayInstallation
from .git_directory_create_config_view import GitDirectoryCreateConfigViewEvent
from .default_dir_content_builder import DefaultDirContentBuilder
from .git_installation import GitDirectorySetupConfiguration
import os

class DefaultOverlayDirContentBuilder(DefaultDirContentBuilder):
    def build_in(self, path: str, repo_name: str):
        """Create default files in given path for new overlay.
        Args:
            path (str): Base directory where the overlay structure will be created.
            repo_name (str): Name of the overlay (used in metadata/layout.conf and profiles/repo_name).
        """
        structure = ['metadata', 'profiles']
        for folder in structure:
            os.makedirs(os.path.join(path, folder), exist_ok=True)
        # Create metadata/layout.conf:
        layout_conf_path = os.path.join(path, 'metadata', 'layout.conf')
        with open(layout_conf_path, 'w') as f:
            f.write(f"repo-name = {repo_name}\n")
        # Create profiles/repo_name:
        repo_name_path = os.path.join(path, 'profiles', 'repo_name')
        with open(repo_name_path, 'w') as f:
            f.write(f"{repo_name}\n")

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/overlay_create/overlay_create_view.ui')
class OverlayCreateView(Gtk.Box):
    __gtype_name__ = "OverlayCreateView"

    # Main views:
    setup_view = Gtk.Template.Child()
    install_view = Gtk.Template.Child()
    # Setup view elements:
    carousel = Gtk.Template.Child()
    config_page = Gtk.Template.Child()
    back_button = Gtk.Template.Child()
    next_button = Gtk.Template.Child()

    def __init__(self, installation_in_progress: OverlayInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.current_page = 0
        self.carousel.connect('page-changed', self.on_page_changed)
        self._set_current_stage(self.installation_in_progress.status if self.installation_in_progress else MultiStageProcessState.SETUP)
        self.install_view.set_multistage_process(self.installation_in_progress)
        self.connect("map", self.on_map)

    def on_map(self, widget):
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
        is_last_page = self.current_page == 1
        self.back_button.set_sensitive(not is_first_page)
        self.back_button.set_opacity(0.0 if is_first_page else 1.0)
        self.next_button.set_sensitive(self.config_page.configuration_ready)
        self.next_button.set_opacity(0.0 if not is_last_page else 1.0)

    @Gtk.Template.Callback()
    def on_back_pressed(self, _):
        is_first_page = self.current_page == 0
        if not is_first_page:
            self.carousel.scroll_to(self.carousel.get_nth_page(self.current_page - 1), True)

    @Gtk.Template.Callback()
    def on_next_pressed(self, _):
        is_last_page = self.current_page == 1
        if not is_last_page:
            self.carousel.scroll_to(self.carousel.get_nth_page(self.current_page + 1), True)
        else:
            configuration = self.config_page.get_configuration(default_dir_content_builder=DefaultOverlayDirContentBuilder())
            self._start_installation(configuration=configuration)

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        self.carousel.scroll_to(self.config_page, True)

    def _set_current_stage(self, stage: MultiStageProcessState):
        # Setup views visibility:
        self.setup_view.set_visible(stage == MultiStageProcessState.SETUP)
        self.install_view.set_visible(stage != MultiStageProcessState.SETUP)

    def _start_installation(self, configuration: GitDirectorySetupConfiguration):
        self.installation_in_progress = OverlayInstallation(configuration=configuration)
        self.installation_in_progress.start()
        self.install_view.set_multistage_process(self.installation_in_progress)
        self._set_current_stage(self.installation_in_progress.status)

