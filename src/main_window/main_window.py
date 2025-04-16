from gi.repository import Adw
from gi.repository import Gtk
from .main_window_side_menu import CatalystlabWindowSideMenu
from .main_window_content import CatalystlabWindowContent
from .main_page import MainPage

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window.ui')
class CatalystlabWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'CatalystlabWindow'

    # Main view - overlay split view.
    split_view = Gtk.Template.Child()
    side_menu = Gtk.Template.Child()
    content_view = Gtk.Template.Child() # Call .load_main_page to set current section.

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Set initial selected page
        self._selected_page = None
        self.selected_page = MainPage.WELCOME

        # Connect row selection change
        self.side_menu.section_list.connect("row-selected", self.on_side_menu_selected)

    @property
    def selected_page(self):
        return self._selected_page

    @selected_page.setter
    def selected_page(self, page: MainPage):
        if self._selected_page == page:
            return

        self._selected_page = page
        self.content_view.load_main_page(page)

        # Highlight the correct button in side menu
        row = self.side_menu.section_list.get_first_child()
        while row:
            if hasattr(row, "page") and row.page == page:
                self.side_menu.section_list.select_row(row)
                break
            row = row.get_next_sibling()

    def on_side_menu_selected(self, listbox, row):
        if row and hasattr(row, "page"):
            self.selected_page = row.page


    # Toggle sidebar visiblity with button.
    @Gtk.Template.Callback()
    def sidebar_toggle_button_clicked(self, button):
        """Callback function that is called when we click the button"""
        self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar())

