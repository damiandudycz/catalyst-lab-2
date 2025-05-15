import sys
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Gio, Adw, Gdk
from .main_window import CatalystlabWindow
from .root_helper_client import RootHelperClient
from .modules_scanner import scan_all_submodules

class CatalystlabApplication(Adw.Application):
    """The main application singleton class."""

    def __init__(self):
        super().__init__(application_id='com.damiandudycz.CatalystLab',
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.create_action('quit', lambda *_: self.quit(), ['<primary>q'])
        self.create_action('about', self.on_about_action)
        self.create_action('preferences', self.on_preferences_action)
        scan_all_submodules("catalystlab")

    def do_activate(self):
        """Called when the application is activated.

        We raise the application's main window, creating it if
        necessary.
        """
        win = self.props.active_window
        if not win:
            win = CatalystlabWindow(application=self)
        win.present()

    def do_shutdown(self):
        """Called when the application is shutting down."""
        RootHelperClient.shared().stop_root_helper()
        Gio.Application.do_shutdown(self)

    def on_about_action(self, widget, _):
        """Callback for the app.about action."""
        about = Adw.AboutWindow(transient_for=self.props.active_window,
                                application_name='catalystlab',
                                application_icon='com.damiandudycz.CatalystLab',
                                developer_name='Unknown',
                                version='0.1.0',
                                developers=['Unknown'],
                                copyright='© 2025 Unknown')
        about.present()

    def on_preferences_action(self, widget, _):
        """Callback for the app.preferences action."""
        print('app.preferences action activated')

    def create_action(self, name, callback, shortcuts=None):
        """Add an application action.

        Args:
            name: the name of the action
            callback: the function to be called when the action is
              activated
            shortcuts: an optional list of accelerators
        """
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)

def main(version):
    """The application's entry point."""
    app = CatalystlabApplication()
    return app.run(sys.argv)
