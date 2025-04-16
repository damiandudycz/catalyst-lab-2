from gi.repository import Gtk
from .main_page import MainPage

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window_side_menu_button.ui')
class MainWindowSideMenuButton(Gtk.ListBoxRow):
    __gtype_name__ = "MainWindowSideMenuButton"

    # Template children
    label = Gtk.Template.Child()
    icon = Gtk.Template.Child()

    def __init__(self, page: MainPage):
        super().__init__()
        self.page = page
        self.set_tooltip_text(page.name)
        self.label.set_label(page.name)
        self.icon.set_from_icon_name(page.icon)

