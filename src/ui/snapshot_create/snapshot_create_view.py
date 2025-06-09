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
    toolsets_list = Gtk.Template.Child()
    back_button = Gtk.Template.Child()
    next_button = Gtk.Template.Child()

    def __init__(self, installation_in_progress: SnapshotInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.selected_toolset: Toolset | None = None
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.current_page = 0
        self.carousel.connect('page-changed', self.on_page_changed)
        self._set_current_stage(self.installation_in_progress.status if self.installation_in_progress else MultiStageProcessState.SETUP)
        self.install_view.set_multistage_process(self.installation_in_progress)
        if installation_in_progress is None or installation_in_progress.status == MultiStageProcessState.SETUP:
            self._fill_toolsets_rows(Repository.Toolset.value)
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
        is_stage_selected = self.selected_toolset is not None
        self.back_button.set_sensitive(not is_first_page)
        self.back_button.set_opacity(0.0 if is_first_page else 1.0)
        self.next_button.set_sensitive(is_stage_selected and is_last_page and not self.selected_toolset.is_reserved)
        self.next_button.set_opacity(0.0 if not is_last_page else 1.0)
        if hasattr(self, 'reserved_label'):
            self.reserved_label.set_visible(self.selected_toolset and self.selected_toolset.is_reserved)

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
            RootHelperClient.shared().authorize_and_run(callback=lambda authorization_keeper: self._start_installation(authorization_keeper=authorization_keeper, selected_toolset=self.selected_toolset))

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

    def _fill_toolsets_rows(self, result: list[Toolset]):
        self.selected_toolset = None
        sorted_toolsets = sorted(result, key=lambda toolset: toolset.metadata.get("date_updated", 0), reverse=True)
        valid_toolsets = [
            toolset for toolset in sorted_toolsets
            if toolset.get_app_install(ToolsetApplication.CATALYST) is not None
        ]
        # Monitor valid toolsets for is_reserved changes
        for toolset in valid_toolsets:
            toolset.event_bus.subscribe(
                ToolsetEvents.IS_RESERVED_CHANGED,
                self.setup_back_next_buttons
            )
        self.selected_toolset = next(
            (toolset for toolset in valid_toolsets if not toolset.is_reserved),
            valid_toolsets[0] if valid_toolsets else None
        )
        if not valid_toolsets:
            error_label = Gtk.Label(label=f"You need to create a toolset with Catalyst installed. Go to Environments section to create such toolset.")
            error_label.set_wrap(True)
            error_label.set_halign(Gtk.Align.CENTER)
            error_label.set_margin_top(12)
            error_label.set_margin_bottom(12)
            error_label.set_margin_start(24)
            error_label.set_margin_end(24)
            error_label.add_css_class("dimmed")
            self.toolsets_list.add(error_label)
        self.reserved_label = Gtk.Label(label="This toolset is currently in use.")
        self.reserved_label.set_wrap(True)
        self.reserved_label.set_halign(Gtk.Align.CENTER)
        self.reserved_label.set_margin_top(12)
        self.reserved_label.set_margin_bottom(12)
        self.reserved_label.set_margin_start(24)
        self.reserved_label.set_margin_end(24)
        self.reserved_label.add_css_class("dimmed")
        self.reserved_label.set_visible(self.selected_toolset and self.selected_toolset.is_reserved)
        self.toolsets_list.add(self.reserved_label)
        toolsets_check_buttons_group = []
        for toolset in sorted_toolsets:
            row = ItemRow(
                item=toolset,
                item_title_property_name="name",
                item_subtitle_property_name="short_details",
                item_status_property_name="status_indicator_values",
                item_icon="preferences-other-symbolic"
            )
            check_button = Gtk.CheckButton()
            check_button.set_active(toolset == self.selected_toolset)
            if toolsets_check_buttons_group:
                check_button.set_group(toolsets_check_buttons_group[0])
            check_button.connect("toggled", self._on_toolset_selected, toolset)
            toolsets_check_buttons_group.append(check_button)
            row.add_prefix(check_button)
            row.set_activatable_widget(check_button)
            row.set_sensitive(toolset.get_app_install(ToolsetApplication.CATALYST) is not None)
            self.toolsets_list.add(row)

    def _on_toolset_selected(self, button: Gtk.CheckButton, toolset: Toolset):
        """Callback for when a row's checkbox is toggled."""
        if button.get_active():
            self.selected_toolset = toolset
        else:
            # Deselect if unchecked
            if self.selected_toolset == toolset:
                self.selected_toolset = None
        self.setup_back_next_buttons()

    def _set_current_stage(self, stage: MultiStageProcessState):
        # Setup views visibility:
        self.setup_view.set_visible(stage == MultiStageProcessState.SETUP)
        self.install_view.set_visible(stage != MultiStageProcessState.SETUP)

