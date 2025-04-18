from gi.repository import Gtk, GObject
from .app_section import AppSection
from .app_section_details import AppSectionDetails
from .main_window_side_menu_button import MainWindowSideMenuButton
from .app_events import EventBus, AppEvents

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window_side_menu.ui')
class CatalystlabWindowSideMenu(Gtk.Box):
    __gtype_name__ = 'CatalystlabWindowSideMenu'

    # View elements:
    section_list = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Load main sections and add buttons for them.
        for section in AppSection:
            section_details = AppSectionDetails.get(section)
            if section_details.show_in_side_bar:
                button = MainWindowSideMenuButton(section)
                self.section_list.append(button)
        EventBus.subscribe(AppEvents.OPEN_APP_SECTION, self.opened_app_section)
        # Set initial selected page
        self.selected_section: AppSectionDetails = None

    def opened_app_section(self, section: AppSection):
        if self.selected_section == section:
            return
        self.selected_section = section
        # Highlight the correct button in side menu
        # Deselect all sections first
        self.section_list.select_row(None)
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
            if row.section != self.selected_section:
                self.selected_section = row.section
                EventBus.emit(AppEvents.OPEN_APP_SECTION, row.section)

