from gi.repository import Adw
from gi.repository import Gtk
from typing import Type
from .main_window_side_menu import CatalystlabWindowSideMenu
from .main_window_content import CatalystlabWindowContent
from .app_section import AppSection
from .app_section_details import AppSectionDetails
from .app_events import EventBus, AppEvents

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window.ui')
class CatalystlabWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'CatalystlabWindow'

    # Static config:
    allow_side_menu_toggle = True # Only for sections that allows that at all.

    # View elements:
    split_view = Gtk.Template.Child()
    side_menu = Gtk.Template.Child()
    content_view = Gtk.Template.Child()
    sidebar_toggle_breakpoint = Gtk.Template.Child()
    sidebar_toggle_button = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        EventBus.subscribe(AppEvents.OPEN_APP_SECTION, self.opened_app_section)
        # Load initial section page:
        EventBus.emit(AppEvents.OPEN_APP_SECTION, AppSectionDetails.initial_section)
        # Connect sidebar_toggle_breakpoint actions
        self.sidebar_toggle_breakpoint.connect("apply", self._on_sidebar_toggle_breakpoint_apply)
        self.sidebar_toggle_breakpoint.connect("unapply", self._on_sidebar_toggle_breakpoint_unapply)

    # Toggle sidebar visibility with button.
    @Gtk.Template.Callback()
    def sidebar_toggle_button_clicked(self, _):
        """Callback function that is called when we click the button"""
        if self.allow_side_menu:
            self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar())

    # Managing side bar and toggle side bar button visibility for selected section and collapsed state:

    def opened_app_section(self, section: AppSection):
        section_details = AppSectionDetails(section)
        self.allow_side_menu = section_details.show_side_bar
        self.split_view.set_show_sidebar(self.allow_side_menu and not self.split_view.get_collapsed())
        self.sidebar_toggle_button.set_visible(self.allow_side_menu and ( self.split_view.get_collapsed() or CatalystlabWindow.allow_side_menu_toggle ))

    def _on_sidebar_toggle_breakpoint_apply(self, breakpoint):
        self.split_view.set_collapsed(True)
        self.split_view.set_show_sidebar(False)
        self.sidebar_toggle_button.set_visible(self.allow_side_menu)

    def _on_sidebar_toggle_breakpoint_unapply(self, breakpoint):
        self.split_view.set_collapsed(False)
        self.split_view.set_show_sidebar(self.allow_side_menu)
        self.sidebar_toggle_button.set_visible(self.allow_side_menu and CatalystlabWindow.allow_side_menu_toggle)
