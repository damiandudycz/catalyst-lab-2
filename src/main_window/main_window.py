from gi.repository import Adw
from gi.repository import Gtk
from .main_window_side_menu import CatalystlabWindowSideMenu
from .main_window_content import CatalystlabWindowContent
from .app_section import AppSection
from .app_events import EventBus, AppEvents

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window.ui')
class CatalystlabWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'CatalystlabWindow'

    # View elements:
    split_view = Gtk.Template.Child()
    side_menu = Gtk.Template.Child()
    content_view = Gtk.Template.Child()
    sidebar_toggle_breakpoint = Gtk.Template.Child()
    sidebar_toggle_button = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Connect views
        self.content_view.side_menu = self.side_menu
        EventBus.subscribe(AppEvents.SET_SIDEBAR_VISIBLE, self.set_sidebar_visible)
        EventBus.subscribe(AppEvents.OPEN_ABOUT, self.open_about_section)
        # Load initial section page.
        self.content_view.load_main_section(self.side_menu.selected_section)
        # Connect sidebar_toggle_breakpoint actions
        self.sidebar_toggle_breakpoint.connect("apply", self._on_sidebar_toggle_breakpoint_apply)
        self.sidebar_toggle_breakpoint.connect("unapply", self._on_sidebar_toggle_breakpoint_unapply)

    # Toggle sidebar visibility with button.
    @Gtk.Template.Callback()
    def sidebar_toggle_button_clicked(self, _):
        """Callback function that is called when we click the button"""
        if self.allow_side_menu:
            self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar())

    # Bind displayed content to side menu selected page.
    @Gtk.Template.Callback()
    def side_menu_row_selected(self, _, section: AppSection):
        self.content_view.load_main_section(section)

    def open_about_section(self, *args, **kwargs):
        self.content_view.load_main_section(AppSection.PROJECTS)

    # Managing side bar and toggle side bar button visibility for selected section and collapsed state:

    def set_sidebar_visible(self, visible: bool):
        self.allow_side_menu = visible
        if (visible and self.allow_side_menu) or not visible:
            self.split_view.set_show_sidebar(visible and not self.split_view.get_collapsed())
            self.sidebar_toggle_button.set_visible(self.allow_side_menu and self.split_view.get_collapsed())

    def _on_sidebar_toggle_breakpoint_apply(self, breakpoint):
        self.split_view.set_collapsed(True)
        self.split_view.set_show_sidebar(False)
        self.sidebar_toggle_button.set_visible(self.allow_side_menu)

    def _on_sidebar_toggle_breakpoint_unapply(self, breakpoint):
        self.split_view.set_collapsed(False)
        self.sidebar_toggle_button.set_visible(False)
        if self.allow_side_menu:
            self.split_view.set_show_sidebar(True)
        else:
            self.split_view.set_show_sidebar(False)

