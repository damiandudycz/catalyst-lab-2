from gi.repository import Adw
from gi.repository import Gtk
from gi.repository import Gio

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window.ui')
class CatalystlabWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'CatalystlabWindow'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Define the window action
        action_toggle_sidebar = Gio.SimpleAction.new("toggle-sidebar", None)
        action_toggle_sidebar.connect("activate", self.on_toggle_sidebar)
        self.add_action(action_toggle_sidebar)

    split_view = Gtk.Template.Child()

    def on_toggle_sidebar(self, action, parameter):
        # Toggle the collapsed state of the split view
        self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar())

