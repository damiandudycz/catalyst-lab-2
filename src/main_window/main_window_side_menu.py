from gi.repository import Gtk
from .main_page import MainPage
from .main_window_side_menu_button import MainWindowSideMenuButton

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window_side_menu.ui')
class CatalystlabWindowSideMenu(Gtk.Box):
    __gtype_name__ = 'CatalystlabWindowSideMenu'

    section_list = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Load main sections and add buttons for them.
        for page in MainPage:
            button = MainWindowSideMenuButton(page)
            self.section_list.append(button)
