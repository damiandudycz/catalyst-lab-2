from gi.repository import Gtk
from .main_page import MainPage

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window_content.ui')
class CatalystlabWindowContent(Gtk.Box):
    """Wrapper container used to display the main content."""
    __gtype_name__ = 'CatalystlabWindowContent'

    content = Gtk.Template.Child()

    def load_main_page(self, page: MainPage):
        """Load content of selected main section."""
        # Get view for given page. TODO: Move to MainPage as method
        builder = Gtk.Builder()
        builder.add_from_resource("/com/damiandudycz/CatalystLab/welcome_page/welcome_page.ui")
        view = builder.get_objects()[0]
        # Display page.
        self.replace_content(view)

    def replace_content(self, new_widget):
        """Replace the current content with a new widget."""
        self.remove(self.content)
        self.append(new_widget)
        self.content = new_widget
