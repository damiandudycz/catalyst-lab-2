from gi.repository import Gtk, GObject
from .main_page import MainPage
from .main_window_side_menu_button import MainWindowSideMenuButton

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window_side_menu.ui')
class CatalystlabWindowSideMenu(Gtk.Box):
    __gtype_name__ = 'CatalystlabWindowSideMenu'

    # View elements:
    section_list = Gtk.Template.Child()

    # Define signals emitted by this widget.
    __gsignals__ = {
        'row-selected': (GObject.SIGNAL_RUN_FIRST, None, (GObject.TYPE_PYOBJECT,))
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Load main sections and add buttons for them.
        for page in MainPage:
            button = MainWindowSideMenuButton(page)
            self.section_list.append(button)
        # Set initial selected page
        self._selected_page = None
        self.selected_page = MainPage.initial_page

    @property
    def selected_page(self):
        return self._selected_page
    @selected_page.setter
    def selected_page(self, page: MainPage):
        if self._selected_page == page:
            return
        self._selected_page = page
        # Pass signal to user of this control
        self.emit("row-selected", page)
        # Highlight the correct button in side menu
        row = self.section_list.get_first_child()
        while row:
            if hasattr(row, "page") and row.page == page:
                self.section_list.select_row(row)
                break
            row = row.get_next_sibling()

    # Callback received when changing selected row. Will be emitted further in @selected_page.setter.
    @Gtk.Template.Callback()
    def row_selected(self, _, row):
        if row and hasattr(row, "page"):
            self.selected_page = row.page

