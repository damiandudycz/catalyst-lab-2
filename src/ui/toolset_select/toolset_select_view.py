from __future__ import annotations
from gi.repository import Gtk, GLib, Gio, GObject
from gi.repository import Adw
from .root_helper_client import RootHelperClient, AuthorizationKeeper
from .multistage_process import MultiStageProcessState
from .toolset import Toolset, ToolsetEvents
from .repository import Repository
from .toolset_application import ToolsetApplication
from .snapshot_installation import SnapshotInstallation
from .repository_list_view import ItemRow
from enum import Enum, auto
from .event_bus import EventBus

class ToolsetSelectionViewEvent(Enum):
    TOOLSET_CHANGED = auto() # Means selection was changed or currently selected state changed.

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/toolset_select/toolset_select_view.ui')
class ToolsetSelectionView(Gtk.Box):
    __gtype_name__ = "ToolsetSelectionView"

    toolsets_list = Gtk.Template.Child()
    required_apps = GObject.Property(type=str, default=None)

    def __init__(self, apps_requirements: [ToolsetApplication] | None = None):
        super().__init__()
        self.apps_requirements = apps_requirements
        self.selected_toolset: Toolset | None = None
        self.event_bus = EventBus[ToolsetSelectionViewEvent]()
        self.connect("realize", self.on_realize)

    def on_realize(self, widget):
        if self.required_apps and self.apps_requirements is None:
            self.apps_requirements = [getattr(ToolsetApplication, value) for value in self.required_apps.split(",")]
        self._fill_toolsets_rows(Repository.Toolset.value)

    def _toolset_is_reserved_changed(self, data):
        self.event_bus.emit(ToolsetSelectionViewEvent.TOOLSET_CHANGED, self)

    def _fill_toolsets_rows(self, result: list[Toolset]):
        self.selected_toolset = None
        sorted_toolsets = sorted(result, key=lambda toolset: toolset.metadata.get("date_updated", 0), reverse=True)
        valid_toolsets = [
            toolset for toolset in sorted_toolsets
            if self.apps_requirements is None or
               all(toolset.get_app_install(app) is not None for app in self.apps_requirements)
        ]
        # Monitor valid toolsets for is_reserved changes
        for toolset in valid_toolsets:
            toolset.event_bus.subscribe(
                ToolsetEvents.IS_RESERVED_CHANGED,
                self._toolset_is_reserved_changed
            )
        self.selected_toolset = next(
            (toolset for toolset in valid_toolsets if not toolset.is_reserved),
            valid_toolsets[0] if valid_toolsets else None
        )
        self.event_bus.emit(ToolsetSelectionViewEvent.TOOLSET_CHANGED, self)
        if not valid_toolsets:
            error_label = Gtk.Label(label=f"You need to create a toolset with apps: {', '.join([app.name for app in self.apps_requirements])}. Go to Environments section to create such toolset.")
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
                item_icon="sledgehammer-svgrepo-com-symbolic"
            )
            check_button = Gtk.CheckButton()
            check_button.set_active(toolset == self.selected_toolset)
            if toolsets_check_buttons_group:
                check_button.set_group(toolsets_check_buttons_group[0])
            check_button.connect("toggled", self._on_toolset_selected, toolset)
            toolsets_check_buttons_group.append(check_button)
            row.add_prefix(check_button)
            row.set_activatable_widget(check_button)
            row.set_sensitive(toolset in valid_toolsets)
            self.toolsets_list.add(row)

    def _on_toolset_selected(self, button: Gtk.CheckButton, toolset: Toolset):
        """Callback for when a row's checkbox is toggled."""
        if button.get_active():
            self.selected_toolset = toolset
        else:
            # Deselect if unchecked
            if self.selected_toolset == toolset:
                self.selected_toolset = None
        self.event_bus.emit(ToolsetSelectionViewEvent.TOOLSET_CHANGED, self)

