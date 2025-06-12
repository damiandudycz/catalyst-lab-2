from __future__ import annotations
from gi.repository import Gtk, GLib, Gio, GObject, Adw
from .repository import Repository
from .repository_list_view import ItemRow
from enum import Enum, auto
from .event_bus import EventBus

class ItemSelectionViewEvent(Enum):
    ITEM_CHANGED = auto() # Means selection was changed or currently selected state changed.

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/item_select/item_select_view.ui')
class tools_list(Gtk.Box):
    __gtype_name__ = "ItemSelectionView"

    __gsignals__ = {
        "is-item-selectable": (GObject.SignalFlags.RUN_FIRST, bool, (GObject.TYPE_PYOBJECT,)),
        "is-item-usable": (GObject.SignalFlags.RUN_FIRST, bool, (GObject.TYPE_PYOBJECT,)),
        "setup-items-monitoring": (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,))
    }

    # View elements:
    items_list = Gtk.Template.Child()
    # Properties:
    item_class_name = GObject.Property(type=str, default=None)
    item_icon = GObject.Property(type=str, default=None)
    item_title_property_name = GObject.Property(type=str, default=None)
    item_subtitle_property_name = GObject.Property(type=str, default=None)
    item_status_property_name = GObject.Property(type=str, default=None)
    autoselect_default = GObject.Property(type=bool, default=False)

    def __init__(self):
        super().__init__()
        self.event_bus = EventBus[ItemSelectionViewEvent]()
        self.connect("realize", self.on_realize)

    def on_realize(self, widget):
        self.item_class = globals().get(self.item_class_name)
        self.repository = getattr(Repository, self.item_class_name)
        self._load_items()

    def select(self, item):
        self.selected_item = item
        self.event_bus.emit(ItemSelectionViewEvent.ITEM_CHANGED, self)

    def refresh_items_state(self, data):
        """Call this from class using this view when monitoring_usable_changes triggers an update"""
        self.is_not_usable_label.set_visible(self.selected_item and not self.emit("is-item-usable", self.selected_item))
        valid_items = [item for item in self.repository.value if self.emit("is-item-selectable", item)]
        self.no_valid_entries_label.set_visible(not valid_items)
        for row in self.rows:
            row.set_sensitive(row.item in valid_items)
        self.event_bus.emit(ItemSelectionViewEvent.ITEM_CHANGED, self)

    def _load_items(self):
        if not hasattr(self, 'selected_item'):
            self.selected_item = None
        valid_items = [item for item in self.repository.value if self.emit("is-item-selectable", item)]
        # Monitor valid items for is_reserved changes
        self.emit("setup-items-monitoring", self.repository.value)
        # Select initial item
        if self.autoselect_default and not self.selected_item:
            self.selected_item = next(
                (item for item in valid_items if self.emit("is-item-usable", item)),
                valid_items[0] if valid_items else None
            )
            self.event_bus.emit(ItemSelectionViewEvent.ITEM_CHANGED, self)
        # No items label
        self.no_valid_entries_label = Gtk.Label(label=f"No valid items available")
        self.no_valid_entries_label.set_wrap(True)
        self.no_valid_entries_label.set_halign(Gtk.Align.CENTER)
        self.no_valid_entries_label.set_margin_top(12)
        self.no_valid_entries_label.set_margin_bottom(12)
        self.no_valid_entries_label.set_margin_start(24)
        self.no_valid_entries_label.set_margin_end(24)
        self.no_valid_entries_label.add_css_class("dimmed")
        self.no_valid_entries_label.set_visible(not valid_items)
        self.items_list.add(self.no_valid_entries_label)
        # Is not usable label
        self.is_not_usable_label = Gtk.Label(label="This item is currently in use.")
        self.is_not_usable_label.set_wrap(True)
        self.is_not_usable_label.set_halign(Gtk.Align.CENTER)
        self.is_not_usable_label.set_margin_top(12)
        self.is_not_usable_label.set_margin_bottom(12)
        self.is_not_usable_label.set_margin_start(24)
        self.is_not_usable_label.set_margin_end(24)
        self.is_not_usable_label.add_css_class("dimmed")
        self.is_not_usable_label.set_visible(self.selected_item and not self.emit("is-item-usable", self.selected_item))
        self.items_list.add(self.is_not_usable_label)
        # Create rows
        items_check_buttons_group = []
        self.rows = []
        for item in self.repository.value:
            row = ItemRow(
                item=item,
                item_title_property_name=self.item_title_property_name,
                item_subtitle_property_name=self.item_subtitle_property_name,
                item_status_property_name=self.item_status_property_name,
                item_icon=self.item_icon
            )
            check_button = Gtk.CheckButton()
            check_button.set_active(item == self.selected_item)
            if items_check_buttons_group:
                check_button.set_group(items_check_buttons_group[0])
            check_button.connect("toggled", self._on_item_selected, item)
            items_check_buttons_group.append(check_button)
            row.add_prefix(check_button)
            row.set_activatable_widget(check_button)
            row.set_sensitive(item in valid_items)
            self.items_list.add(row)
            self.rows.append(row)

    def _on_item_selected(self, button: Gtk.CheckButton, item: item):
        """Callback for when a row's checkbox is toggled."""
        if button.get_active():
            self.selected_item = item
        else:
            if self.selected_item == item:
                self.selected_item = None
        # TODO: This emits wtice - for deselecting previous and selecting new
        self.event_bus.emit(ItemSelectionViewEvent.ITEM_CHANGED, self)

