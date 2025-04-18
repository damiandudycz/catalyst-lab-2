from gi.repository import Gtk
from .app_section import AppSection

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window_content.ui')
class CatalystlabWindowContent(Gtk.Box):
    """Wrapper container used to display the main content."""
    __gtype_name__ = 'CatalystlabWindowContent'

    # View elements:
    content = Gtk.Template.Child()

    def load_main_section(self, section: AppSection):
        """Load content of selected main section."""
        # Display section.
        section_widget = section.create_section()
        self.replace_content(section_widget)

    def replace_content(self, new_widget: Gtk.Widget):
        """Replace the current content with a new widget."""
        self.remove(self.content)
        self.append(new_widget)
        self.content = new_widget
