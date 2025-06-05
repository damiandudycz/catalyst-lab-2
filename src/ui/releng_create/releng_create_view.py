from __future__ import annotations
from gi.repository import Gtk, GLib, Gio, Adw
from .multistage_process import MultiStageProcessState
from .releng_installation import RelengInstallation
from .releng_directory import RelengDirectory
from .releng_manager import RelengManager
import os

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/releng_create/releng_create_view.ui')
class RelengCreateView(Gtk.Box):
    __gtype_name__ = "RelengCreateView"

    # Main views:
    setup_view = Gtk.Template.Child()
    install_view = Gtk.Template.Child()
    # Setup view elements:
    carousel = Gtk.Template.Child()
    config_page = Gtk.Template.Child()
    directory_name_row = Gtk.Template.Child()
    name_used_label = Gtk.Template.Child()
    back_button = Gtk.Template.Child()
    next_button = Gtk.Template.Child()

    def __init__(self, installation_in_progress: RelengInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.current_page = 0
        self.carousel.connect('page-changed', self.on_page_changed)
        self._set_current_stage(self.installation_in_progress.status if self.installation_in_progress else MultiStageProcessState.SETUP)
        self.install_view.set_multistage_process(self.installation_in_progress)
        self.check_filename_is_free()
        self.connect("map", self.on_map)

    def on_map(self, widget):
        self.install_view.content_navigation_view = self.content_navigation_view
        self.install_view._window = self._window

    def on_page_changed(self, carousel, pspec):
        self.current_page = int(carousel.get_position())
        self.setup_back_next_buttons()

    def setup_back_next_buttons(self, _ = None):
        is_first_page = self.current_page == 0
        is_last_page = self.current_page == 1
        self.back_button.set_sensitive(not is_first_page)
        self.back_button.set_opacity(0.0 if is_first_page else 1.0)
        self.next_button.set_sensitive(self.filename_is_free)
        self.next_button.set_opacity(0.0 if not is_last_page else 1.0)

    def check_filename_is_free(self) -> bool:
        self.filename_is_free = RelengManager.shared().is_name_available(name=self.directory_name_row.get_text())
        self.setup_back_next_buttons()
        self.name_used_label.set_visible(not self.filename_is_free)
        return self.filename_is_free

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
            self._start_installation(name=self.directory_name_row.get_text())

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        self.carousel.scroll_to(self.config_page, True)

    def _set_current_stage(self, stage: MultiStageProcessState):
        # Setup views visibility:
        self.setup_view.set_visible(stage == MultiStageProcessState.SETUP)
        self.install_view.set_visible(stage != MultiStageProcessState.SETUP)

    @Gtk.Template.Callback()
    def on_directory_name_activate(self, sender):
        self.check_filename_is_free()
        self.get_root().set_focus(None)

    @Gtk.Template.Callback()
    def on_directory_name_changed(self, sender):
        self.check_filename_is_free()

    def _start_installation(self, name: str):
        if not self.check_filename_is_free():
            return
        self.installation_in_progress = RelengInstallation(name=name)
        self.installation_in_progress.start()
        self.install_view.set_multistage_process(self.installation_in_progress)
        self._set_current_stage(self.installation_in_progress.status)

