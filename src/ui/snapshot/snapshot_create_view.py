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
from .item_select_view import ItemSelectionViewEvent
from .wizard_view import WizardView

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/snapshot/snapshot_create_view.ui')
class SnapshotCreateView(Gtk.Box):
    __gtype_name__ = "SnapshotCreateView"

    # Main views:
    wizard_view = Gtk.Template.Child()
    # Setup view elements:
    config_page = Gtk.Template.Child()
    toolset_selection_view = Gtk.Template.Child()

    def __init__(self, installation_in_progress: SnapshotInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.apps_requirements = [ToolsetApplication.CATALYST]
        self.connect("realize", self.on_realize)
        self.toolset_selection_view.event_bus.subscribe(
            ItemSelectionViewEvent.ITEM_CHANGED,
            self.toolset_changed
        )

    def toolset_changed(self, data):
        self.wizard_view._refresh_buttons_state()

    def on_realize(self, widget):
        self.wizard_view.content_navigation_view = self.content_navigation_view
        self.wizard_view._window = self._window
        self.wizard_view.set_installation(self.installation_in_progress)

    @Gtk.Template.Callback()
    def is_item_selectable(self, sender, item) -> bool:
        return all(item.get_app_install(app) is not None for app in self.apps_requirements)

    @Gtk.Template.Callback()
    def is_item_usable(self, sender, item) -> bool:
        return not item.is_reserved

    @Gtk.Template.Callback()
    def setup_items_monitoring(self, sender, items):
        for item in items:
            item.event_bus.subscribe(
                ToolsetEvents.IS_RESERVED_CHANGED,
                self.toolset_selection_view.refresh_items_state
            )

    @Gtk.Template.Callback()
    def is_page_ready_to_continue(self, sender, page) -> bool:
        match page:
            case self.config_page:
                return (
                    self.toolset_selection_view.selected_item is not None
                    and self.is_item_usable(self.toolset_selection_view, self.toolset_selection_view.selected_item)
                    and self.is_item_selectable(self.toolset_selection_view, self.toolset_selection_view.selected_item)
                )
        return True

    @Gtk.Template.Callback()
    def begin_installation(self, view):
        RootHelperClient.shared().authorize_and_run(callback=lambda authorization_keeper: self._start_installation(authorization_keeper=authorization_keeper, selected_toolset=self.toolset_selection_view.selected_item))

    def _start_installation(self, authorization_keeper: AuthorizationKeeper, selected_toolset: Toolset | None = None, selected_file: Gio.File | None = None):
        if not authorization_keeper:
            return
        installation_in_progress = SnapshotInstallation(
            toolset=selected_toolset,
            file=selected_file
        )
        installation_in_progress.start()
        self.wizard_view.set_installation(installation_in_progress)

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

