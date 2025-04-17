from gi.repository import Gtk, GObject
from .main_section import MainSection
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
        for section in MainSection:
            button = MainWindowSideMenuButton(section)
            self.section_list.append(button)
        # Set initial selected page
        self._selected_section: MainSection = None
        self.selected_section = MainSection.initial_section

    @property
    def selected_section(self):
        return self._selected_section
    @selected_section.setter
    def selected_section(self, section: MainSection):
        if self._selected_section == section:
            return
        self._selected_section = section
        # Pass signal to user of this control
        self.emit("row-selected", section)
        # Highlight the correct button in side menu
        row = self.section_list.get_first_child()
        while row:
            if hasattr(row, "section") and row.section == section:
                self.section_list.select_row(row)
                break
            row = row.get_next_sibling()

    # Callback received when changing selected row. Will be emitted further in @selected_section.setter.
    @Gtk.Template.Callback()
    def row_selected(self, _, row):
        if row and hasattr(row, "section"):
            self.selected_section = row.section

