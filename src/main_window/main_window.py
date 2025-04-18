from gi.repository import Adw
from gi.repository import Gtk
from .main_window_side_menu import CatalystlabWindowSideMenu
from .main_window_content import CatalystlabWindowContent
from .app_section import AppSection

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window.ui')
class CatalystlabWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'CatalystlabWindow'

    # View elements:
    split_view = Gtk.Template.Child()
    side_menu = Gtk.Template.Child()
    content_view = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Load initial section page.
        self.content_view.load_main_section(self.side_menu.selected_section)

    # Toggle sidebar visibility with button.
    @Gtk.Template.Callback()
    def sidebar_toggle_button_clicked(self, _):
        """Callback function that is called when we click the button"""
        self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar())

    # Bind displayed content to side menu selected page.
    @Gtk.Template.Callback()
    def side_menu_row_selected(self, _, section: AppSection):
        self.content_view.load_main_section(section)

