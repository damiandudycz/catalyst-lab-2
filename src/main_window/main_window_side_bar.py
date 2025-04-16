from gi.repository import Adw
from gi.repository import Gtk

from .main_window_side_menu import CatalystlabWindowSideMenu

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window_side_bar.ui')
class CatalystlabWindowSideBar(Adw.NavigationPage):
    __gtype_name__ = 'CatalystlabWindowSideBar'

