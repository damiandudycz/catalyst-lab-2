from __future__ import annotations
from gi.repository import Gtk, GLib, Gio, GObject, Adw
from enum import Enum, auto
from .repository import Repository
from .repository_list_view import ItemRow
from .event_bus import EventBus

class ItemSelectionViewEvent(Enum):
    ITEM_CHANGED = auto() # Means selection was changed or currently selected state changed.

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/components/item_select/item_select_view.ui')
class ItemSelectionView(Gtk.Box):
    __gtype_name__ = "ItemSelectionView"

    __gsignals__ = {
        "is-item-selectable": (GObject.SignalFlags.RUN_FIRST, bool, (GObject.TYPE_PYOBJECT,)),
        "is-item-usable": (GObject.SignalFlags.RUN_FIRST, bool, (GObject.TYPE_PYOBJECT,)),
        "setup-items-monitoring": (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,))
    }

    # View elements:
    items_list = Gtk.Template.Child()
    # Properties:
    title = GObject.Property(type=str, default=None) # TODO: Display
    item_class_name = GObject.Property(type=str, default=None)
    item_icon = GObject.Property(type=str, default=None)
    item_title_property_name = GObject.Property(type=str, default=None)
    item_subtitle_property_name = GObject.Property(type=str, default=None)
    item_status_property_name = GObject.Property(type=str, default=None)
    autoselect_default = GObject.Property(type=bool, default=False)
    display_none = GObject.Property(type=bool, default=False)
    none_title = GObject.Property(type=str, default="None")
    none_subtitle = GObject.Property(type=str, default=None)

    def __init__(self):
        super().__init__()
        self.selected_item = None
        self.event_bus = EventBus[ItemSelectionViewEvent]()
        self._load_labels()
        self.connect("realize", self.on_realize)

    def set_static_list(self, list: list):
        """Use static list insetad of repository. Only call this once."""
        self.static_list = list
        self._load_items()

    def on_realize(self, widget):
        if self.item_class_name:
            self.item_class = globals().get(self.item_class_name)
            self.repository = getattr(Repository, self.item_class_name)
            if hasattr(self, 'static_list'):
                raise ValueError("Canno't use both static_list and item_class_name")
            self._load_items()

    def items(self) -> list:
        return self.static_list if hasattr(self, 'static_list') else self.repository.value

    def select(self, item):
        self.selected_item = item
        self.event_bus.emit(ItemSelectionViewEvent.ITEM_CHANGED, self)

    def refresh_items_state(self, data):
        """Call this from class using this view when monitoring_usable_changes triggers an update"""
        self.is_not_usable_label.set_visible(self.selected_item and not self.emit("is-item-usable", self.selected_item))
        valid_items = [item for item in self.items() if self.emit("is-item-selectable", item)]
        self.no_valid_entries_label.set_visible(not valid_items)
        for row in self.rows:
            row.set_sensitive(row.item in valid_items)
        self.event_bus.emit(ItemSelectionViewEvent.ITEM_CHANGED, self)

    def _load_labels(self):
        # No items label
        self.no_valid_entries_label = Gtk.Label(label=f"No valid items available")
        self.no_valid_entries_label.set_wrap(True)
        self.no_valid_entries_label.set_halign(Gtk.Align.CENTER)
        self.no_valid_entries_label.set_margin_top(12)
        self.no_valid_entries_label.set_margin_bottom(12)
        self.no_valid_entries_label.set_margin_start(24)
        self.no_valid_entries_label.set_margin_end(24)
        self.no_valid_entries_label.add_css_class("dimmed")
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
        self.items_list.add(self.is_not_usable_label)

    def _load_items(self):
        valid_items = [item for item in self.items() if self.emit("is-item-selectable", item)]
        # Monitor valid items for is_reserved changes
        self.emit("setup-items-monitoring", self.items())
        # Select initial item
        if self.autoselect_default:
            self.selected_item = next(
                (item for item in valid_items if self.emit("is-item-usable", item)),
                valid_items[0] if valid_items else None
            )
            self.event_bus.emit(ItemSelectionViewEvent.ITEM_CHANGED, self)
        # Create rows
        items_check_buttons_group = []

        hidden_check_box = Gtk.CheckButton()
        items_check_buttons_group.append(hidden_check_box)

        if hasattr(self, 'rows'):
            for row in self.rows:
                self.items_list.remove(row)
        if hasattr(self, 'none_row') and self.none_row:
            self.remove(self.none_row)
            del self.none_row

        if self.display_none and not hasattr(self, 'none_row'):
            self.none_row = Adw.ActionRow(title=self.none_title, subtitle=self.none_subtitle)
            check_button = Gtk.CheckButton()
            check_button.set_active(self.selected_item == None)
            check_button.connect("toggled", self._on_item_selected, None)
            if items_check_buttons_group:
                check_button.set_group(items_check_buttons_group[0])
            items_check_buttons_group.append(check_button)
            self.none_row.add_prefix(check_button)
            self.none_row.set_activatable_widget(check_button)
            self.items_list.add(self.none_row)

        self.rows = []
        for item in self.items():
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
        self.no_valid_entries_label.set_visible(not valid_items)
        self.is_not_usable_label.set_visible(self.selected_item and not self.emit("is-item-usable", self.selected_item))

    def _on_item_selected(self, button: Gtk.CheckButton, item: item):
        """Callback for when a row's checkbox is toggled."""
        if button.get_active():
            self.selected_item = item
        else:
            if len(self.rows) == 1 and not self.display_none or len(self.rows) == 0 and self.display_none:
                button.set_active(True)
        # Schedule a single emission in the idle loop
        if not hasattr(self, '_emit_idle_id'):
            self._emit_idle_id = None
        if self._emit_idle_id is None:
            self._emit_idle_id = GLib.idle_add(self._emit_selection_change)

    def _emit_selection_change(self):
        self._emit_idle_id = None
        self.event_bus.emit(ItemSelectionViewEvent.ITEM_CHANGED, self)
        return False

