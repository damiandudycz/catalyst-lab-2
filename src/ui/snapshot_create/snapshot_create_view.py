from __future__ import annotations
from gi.repository import Gtk, GLib, Gio
from gi.repository import Adw
from .root_helper_client import RootHelperClient, AuthorizationKeeper
from .multistage_process import MultiStageProcessState
from .toolset import Toolset, ToolsetEvents
from .repository import Repository
from .toolset_application import ToolsetApplication
from .snapshot_installation import SnapshotInstallation
from .repository_list_view import ItemRow
from .toolset_select_view import ToolsetSelectionViewEvent

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/snapshot_create/snapshot_create_view.ui')
class SnapshotCreateView(Gtk.Box):
    __gtype_name__ = "SnapshotCreateView"

    # Main views:
    setup_view = Gtk.Template.Child()
    install_view = Gtk.Template.Child()
    # Setup view elements:
    carousel = Gtk.Template.Child()
    welcome_page = Gtk.Template.Child()
    config_page = Gtk.Template.Child()
    toolset_selection_view = Gtk.Template.Child()
    back_button = Gtk.Template.Child()
    next_button = Gtk.Template.Child()

    def __init__(self, installation_in_progress: SnapshotInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
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

    def on_page_changed(self, carousel, pspec):
        self.current_page = int(carousel.get_position())
        self.setup_back_next_buttons()

    def setup_back_next_buttons(self, _ = None):
        is_first_page = self.current_page == 0
        is_last_page = self.current_page == 1
        is_stage_selected = self.toolset_selection_view.selected_toolset is not None
        self.back_button.set_sensitive(not is_first_page)
        self.back_button.set_opacity(0.0 if is_first_page else 1.0)
        self.next_button.set_sensitive(is_stage_selected and is_last_page and not self.toolset_selection_view.selected_toolset.is_reserved)
        self.next_button.set_opacity(0.0 if not is_last_page else 1.0)
        if hasattr(self, 'reserved_label'):
            self.reserved_label.set_visible(self.toolset_selection_view.selected_toolset and self.toolset_selection_view.selected_toolset.is_reserved)

    @Gtk.Template.Callback()
    def on_select_file_pressed(self, _):
        def on_file_open_response(file_dialog, result):
            try:
                selected_file = file_dialog.open_finish(result)
                RootHelperClient.shared().authorize_and_run(
                    callback=lambda authorization_keeper: self._start_installation(
                        authorization_keeper=authorization_keeper, selected_file=selected_file
                    )
                )
            except GLib.Error as e:
                print("File open canceled or failed:", e)
        def create_file_filter():
            file_filter = Gtk.FileFilter()
            file_filter.set_name("Squashfs files (*.sqfs)")
            file_filter.add_pattern("*.sqfs")
            return file_filter
        def create_filter_list():
            store = Gio.ListStore.new(Gtk.FileFilter)
            store.append(create_file_filter())
            return store
        file_dialog = Gtk.FileDialog.new()
        file_dialog.set_title("Select a .sqfs file")
        filters = create_filter_list()
        file_dialog.set_filters(filters)
        file_dialog.open(getattr(self, '_window', None) or self.get_root(), None, on_file_open_response)

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
            RootHelperClient.shared().authorize_and_run(callback=lambda authorization_keeper: self._start_installation(authorization_keeper=authorization_keeper, selected_toolset=self.toolset_selection_view.selected_toolset))

    def _start_installation(self, authorization_keeper: AuthorizationKeeper, selected_toolset: Toolset | None = None, selected_file: Gio.File | None = None):
        if not authorization_keeper:
            return
        self.installation_in_progress = SnapshotInstallation(
            toolset=selected_toolset,
            file=selected_file
        )
        self.installation_in_progress.start(authorization_keeper=authorization_keeper)
        self.install_view.set_multistage_process(self.installation_in_progress)
        self._set_current_stage(self.installation_in_progress.status)

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        self.carousel.scroll_to(self.config_page, True)

    def _set_current_stage(self, stage: MultiStageProcessState):
        # Setup views visibility:
        self.setup_view.set_visible(stage == MultiStageProcessState.SETUP)
        self.install_view.set_visible(stage != MultiStageProcessState.SETUP)

