from __future__ import annotations
from gi.repository import Gtk, GLib, Gio, GObject, Adw
from .snapshot import Snapshot
from .repository import Repository
from .repository_list_view import ItemRow
from enum import Enum, auto
from .event_bus import EventBus

class SnapshotSelectionViewEvent(Enum):
    SELECTION_CHANGED = auto() # Means selection was changed

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/snapshot_select/snapshot_select_view.ui')
class SnapshotSelectionView(Gtk.Box):
    __gtype_name__ = "SnapshotSelectionView"

    snapshots_list = Gtk.Template.Child()

    def __init__(self):
        super().__init__()
        self.selected_snapshot: Snapshot | None = None
        self.event_bus = EventBus[SnapshotSelectionViewEvent]()
        self.connect("realize", self.on_realize)

    def on_realize(self, widget):
        self._fill_rows(Repository.Snapshot.value)

    def _fill_rows(self, result: list[Snapshot]):
        self.selected_snapshot = next((snapshot for snapshot in result), None)
        self.event_bus.emit(SnapshotSelectionViewEvent.SELECTION_CHANGED, self)
        if not result:
            error_label = Gtk.Label(label="You need to create a Portage snapshot. Go to Snapshots section to create it.")
            error_label.set_wrap(True)
            error_label.set_halign(Gtk.Align.CENTER)
            error_label.set_margin_top(12)
            error_label.set_margin_bottom(12)
            error_label.set_margin_start(24)
            error_label.set_margin_end(24)
            error_label.add_css_class("dimmed")
            self.snapshots_list.add(error_label)
        snapshots_check_buttons_group = []
        for snapshot in result:
            row = ItemRow(
                item=snapshot,
                item_title_property_name="name",
                item_subtitle_property_name="short_details",
                item_status_property_name="status_indicator_values",
                item_icon="video-frame-svgrepo-com-symbolic"
            )
            check_button = Gtk.CheckButton()
            check_button.set_active(snapshot == self.selected_snapshot)
            if snapshots_check_buttons_group:
                check_button.set_group(snapshots_check_buttons_group[0])
            check_button.connect("toggled", self._on_snapshot_selected, snapshot)
            snapshots_check_buttons_group.append(check_button)
            row.add_prefix(check_button)
            row.set_activatable_widget(check_button)
            row.set_sensitive(True)
            self.snapshots_list.add(row)

    def _on_snapshot_selected(self, button: Gtk.CheckButton, snapshot: Snapshot):
        """Callback for when a row's checkbox is toggled."""
        if button.get_active():
            self.selected_snapshot = snapshot
        else:
            # Deselect if unchecked
            if self.selected_snapshot == snapshot:
                self.selected_snapshot = None
        self.event_bus.emit(SnapshotSelectionViewEvent.SELECTION_CHANGED, self)

