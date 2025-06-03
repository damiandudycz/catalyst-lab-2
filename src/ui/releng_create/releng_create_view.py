from __future__ import annotations
from gi.repository import Gtk, GLib, Gio, Adw
from .multistage_process import MultiStageProcessState

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/releng_create/releng_create_view.ui')
class RelengCreateView(Gtk.Box):
    __gtype_name__ = "RelengCreateView"

    # Main views:
    setup_view = Gtk.Template.Child()
    install_view = Gtk.Template.Child()
    # Setup view elements:
    carousel = Gtk.Template.Child()

    back_button = Gtk.Template.Child()
    next_button = Gtk.Template.Child()

    def __init__(self, installation_in_progress: RelengInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.carousel.connect('page-changed', self.on_page_changed)
        self._set_current_stage(self.installation_in_progress.status if self.installation_in_progress else MultiStageProcessState.SETUP)
        self.install_view.set_multistage_process(self.installation_in_progress)
        # ...
        self.connect("map", self.on_map)

    def on_map(self, widget):
        self.install_view.content_navigation_view = self.content_navigation_view
        self.install_view._window = self._window

    def on_page_changed(self, carousel, pspec):
        self.current_page = int(carousel.get_position())
        self.setup_back_next_buttons()

    def setup_back_next_buttons(self, _ = None):
        is_first_page = self.current_page == 0
        is_second_page = self.current_page == 0
        is_last_page = self.current_page == 2
        self.back_button.set_sensitive(not is_first_page)
        self.back_button.set_opacity(0.0 if is_first_page else 1.0)
        self.next_button.set_sensitive(True)
        self.next_button.set_opacity(0.0 if not is_last_page else 1.0)
        self.next_button.set_label("Create releng directory" if is_last_page else "Next")

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
#        else:
#            RootHelperClient.shared().authorize_and_run(callback=self._start_installation)

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        self.carousel.scroll_to(self.source_page, True)


    def _set_current_stage(self, stage: MultiStageProcessState):
        # Setup views visibility:
        self.setup_view.set_visible(stage == MultiStageProcessState.SETUP)
        self.install_view.set_visible(stage != MultiStageProcessState.SETUP)

