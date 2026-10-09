from gi.repository import Gtk
from .app_section import AppSection
from .status_indicator import StatusIndicator, StatusIndicatorValues

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/main_window/main_window_side_menu_button.ui')
class MainWindowSideMenuButton(Gtk.ListBoxRow):
    __gtype_name__ = "MainWindowSideMenuButton"

    # Template children
    label = Gtk.Template.Child()
    icon = Gtk.Template.Child()

    def __init__(self, section: AppSection):
        super().__init__()
        self.section = section
        self.set_tooltip_text(section.section_details.label)
        self.label.set_label(section.section_details.label)
        self.icon.set_from_icon_name(section.section_details.icon)
        self.status_indicator = StatusIndicator()
        self.status_indicator.set_valign(Gtk.Align.CENTER)
        self.status_indicator.set_visible(False)
        self.label.get_parent().append(self.status_indicator)


    def update_status(self):
        """Shows status of section (running operations, unsaved changes...), like status of its items."""
        status_function = getattr(self.section, "section_status", None)
        try:
            values: StatusIndicatorValues | None = status_function() if status_function else None
        except Exception as e:
            print(f"Failed to get status of section {self.section.section_details.title}: {e}")
            values = None
        self.status_indicator.set_visible(values is not None)
        if values is not None and values != getattr(self, "_status_values", None):
            self.status_indicator.set_values(values)
        self._status_values = values
