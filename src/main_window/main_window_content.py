from gi.repository import Gtk
from gi.repository import Adw
from .app_section import AppSection
from .app_section_details import AppSectionDetails
from .main_window_side_menu import CatalystlabWindowSideMenu
from .app_events import EventBus, AppEvents

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window_content.ui')
class CatalystlabWindowContent(Gtk.Box):
    """Wrapper container used to display the main content."""
    __gtype_name__ = 'CatalystlabWindowContent'

    # View elements:
    content = Gtk.Template.Child()

    def open_app_section(self, section: AppSection, content_navigation_view: Adw.NavigationView):
        """Load content of selected main section."""
        # Display section.
        section_details = AppSectionDetails.init_from(section)
        section_widget = section_details.create_section(content_navigation_view=content_navigation_view)
        self.replace_content(section_widget)

    def replace_content(self, new_widget: Gtk.Widget):
        """Replace the current content with a new widget."""
        self.remove(self.content)
        self.append(new_widget)
        self.content = new_widget

