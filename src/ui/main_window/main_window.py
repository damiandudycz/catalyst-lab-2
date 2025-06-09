from gi.repository import Adw, Gtk
from .app_section import AppSection
from .app_events import AppEvents, app_event_bus
from .navigation_view_extensions import *

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/main_window/main_window.ui')
class CatalystlabWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'CatalystlabWindow'

    # Static config:
    allow_side_menu_toggle = True # Only for sections that allows that at all.

    # View elements:
    navigation_view = Gtk.Template.Child() # Main full window navigation_view
    split_view = Gtk.Template.Child()
    content_view = Gtk.Template.Child()
    side_menu = Gtk.Template.Child()
    sidebar_toggle_breakpoint = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        app_event_bus.subscribe(AppEvents.OPEN_APP_SECTION, self.opened_app_section)
        app_event_bus.subscribe(AppEvents.PUSH_VIEW, self.navigation_view.push_view)
        app_event_bus.subscribe(AppEvents.PUSH_SECTION, self.navigation_view.push_section)
        app_event_bus.subscribe(AppEvents.PRESENT_VIEW, self._present_view)
        app_event_bus.subscribe(AppEvents.PRESENT_SECTION, self._present_section)
        # Load initial section page:
        app_event_bus.emit(AppEvents.OPEN_APP_SECTION, AppSection.WelcomeSection)
        # Connect sidebar_toggle_breakpoint actions
        self.sidebar_toggle_breakpoint.connect("apply", self._on_sidebar_toggle_breakpoint_apply)
        self.sidebar_toggle_breakpoint.connect("unapply", self._on_sidebar_toggle_breakpoint_unapply)

    # Toggle sidebar visibility with button.
    @Gtk.Template.Callback()
    def sidebar_toggle_button_clicked(self, window_content, button):
        """Callback function that is called when we click the button"""
        if self.allow_side_menu:
            self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar())

    # Managing side bar and toggle side bar button visibility for selected section and collapsed state:

    def opened_app_section(self, section: AppSection):
        self.content_view.open_app_section(section)
        self.allow_side_menu = section.section_details.show_side_bar
        self.split_view.set_show_sidebar(self.allow_side_menu and not self.split_view.get_collapsed())
        self.content_view.sidebar_toggle_button_visible = self.allow_side_menu and ( self.split_view.get_collapsed() or CatalystlabWindow.allow_side_menu_toggle )
        self.navigation_view.pop_to_tag("root")

    def _on_sidebar_toggle_breakpoint_apply(self, breakpoint):
        self.split_view.set_collapsed(True)
        self.split_view.set_show_sidebar(False)
        self.content_view.sidebar_toggle_button_visible = self.allow_side_menu

    def _on_sidebar_toggle_breakpoint_unapply(self, breakpoint):
        self.split_view.set_collapsed(False)
        self.split_view.set_show_sidebar(self.allow_side_menu)
        self.content_view.sidebar_toggle_button_visible = self.allow_side_menu and CatalystlabWindow.allow_side_menu_toggle

    def _present_section(self, section: AppSection, **kwargs):
        view = section(content_navigation_view=None, **kwargs)
        title = section.section_details.title
        self._present_view(view, title, 640, 480)

    def _present_view(self, view: Gtk.Widget, title: str, width: int = -1, height: int = -1):
        header = Adw.HeaderBar()
        toolbar_view = Adw.ToolbarView()
        # Container centers the content.
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        container.append(view)
        toolbar_view.set_content(container)
        toolbar_view.add_top_bar(header)
        window = Adw.Window()
        window.set_transient_for(self.get_root())
        window.set_modal(True)
        window.set_title(title)
        if hasattr(view, "content_navigation_view"):
            # If view supports content_navigation_view (is section) embed it into new nav_view
            nav_view = Adw.NavigationView()
            view.content_navigation_view = nav_view
            page = Adw.NavigationPage.new(toolbar_view, title)
            nav_view.push(page)
            window.set_content(nav_view)
        else:
            window.set_content(toolbar_view)
        window.set_default_size(width, height)
        view._window = window
        window.present()
