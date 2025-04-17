from gi.repository import Gtk
from typing import Type
from .main_section import MainSection

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window_side_menu_button.ui')
class MainWindowSideMenuButton(Gtk.ListBoxRow):
    __gtype_name__ = "MainWindowSideMenuButton"

    # Template children
    label = Gtk.Template.Child()
    icon = Gtk.Template.Child()

    def __init__(self, section: Type[MainSection]):
        super().__init__()
        self.section = section
        self.set_tooltip_text(section.label)
        self.label.set_label(section.label)
        self.icon.set_from_icon_name(section.icon)

