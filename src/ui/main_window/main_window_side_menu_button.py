from gi.repository import Gtk
from .app_section import AppSection
from .app_section_details import AppSectionDetails

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/main_window/main_window_side_menu_button.ui')
class MainWindowSideMenuButton(Gtk.ListBoxRow):
    __gtype_name__ = "MainWindowSideMenuButton"

    # Template children
    label = Gtk.Template.Child()
    icon = Gtk.Template.Child()

    def __init__(self, section: AppSection):
        super().__init__()
        self.section = section
        section_details = AppSectionDetails.init_from(section)
        self.set_tooltip_text(section_details.label)
        self.label.set_label(section_details.label)
        self.icon.set_from_icon_name(section_details.icon)

