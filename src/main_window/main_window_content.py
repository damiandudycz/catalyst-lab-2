from gi.repository import Gtk
from .app_section import AppSection
from .main_window_side_menu import CatalystlabWindowSideMenu

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window_content.ui')
class CatalystlabWindowContent(Gtk.Box):
    """Wrapper container used to display the main content."""
    __gtype_name__ = 'CatalystlabWindowContent'

    # View elements:
    content = Gtk.Template.Child()
    side_menu: CatalystlabWindowSideMenu

    def load_main_section(self, section: AppSection):
        """Load content of selected main section."""
        # Display section.
        section_widget = section.create_section()
        self.replace_content(section_widget)
        # TODO: Pass new selection to side menu
        self.side_menu.selected_section = section

    def replace_content(self, new_widget: Gtk.Widget):
        """Replace the current content with a new widget."""
        self.remove(self.content)
        self.append(new_widget)
        self.content = new_widget
        # Content bindings:
        # Detect if displayed content has special properties used for binding application elements and connect them.
        # For example passing reference to content_view, side_menu, app, etc.
        # These bindings can be used to manage app navigation from within displayed content.
