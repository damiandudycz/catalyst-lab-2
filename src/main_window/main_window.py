from gi.repository import Adw
from gi.repository import Gtk
from .main_window_side_menu import CatalystlabWindowSideMenu
from .main_window_content import CatalystlabWindowContent

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window.ui')
class CatalystlabWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'CatalystlabWindow'

    # Main view - overlay split view.
    split_view = Gtk.Template.Child()
    content_view = Gtk.Template.Child() # Call .replace_content to set current content.

    # Toggle sidebar visiblity with button.
    @Gtk.Template.Callback()
    def sidebar_toggle_button_clicked(self, button):
        """Callback function that is called when we click the button"""
        self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar())

