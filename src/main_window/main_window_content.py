from gi.repository import Gtk

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window_content.ui')
class CatalystlabWindowContent(Gtk.Box):
    """Wrapper container used to display the main content."""
    __gtype_name__ = 'CatalystlabWindowContent'

    content = Gtk.Template.Child()

    def replace_content(self, new_widget):
        """Replace the current content with a new widget."""
        self.remove(self.content)
        self.append(new_widget)
