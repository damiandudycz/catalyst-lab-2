import sys
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Gio, Adw, Gdk
from .main_window import CatalystlabWindow
from .modules_scanner import scan_all_submodules
from . import app_info
from .root_helper_client import RootHelperClient
from .toolset_manager import ToolsetManager
from .snapshot_manager import SnapshotManager
from .releng_manager import RelengManager
from .overlay_manager import OverlayManager
from .project_manager import ProjectManager

class CatalystlabApplication(Adw.Application):
    """The main application singleton class."""

    def __init__(self):
        super().__init__(application_id='com.damiandudycz.CatalystLab',
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.create_action('quit', lambda *_: self.quit(), ['<primary>q'])
        self.create_action('about', self.on_about_action)
        self.create_action('preferences', self.on_preferences_action)
        scan_all_submodules("catalystlab")
        # Before any operation starts, they keep their data there.
        from .rootless import clear_temporary_directory
        clear_temporary_directory()
        ToolsetManager.shared().refresh()
        SnapshotManager.shared().refresh()
        RelengManager.shared().refresh()
        OverlayManager.shared().refresh()
        ProjectManager.shared().refresh()
        # Projects get binary packages folders, packages built before are moved to them.
        from .packages_directory import migrate_project_packages
        migrate_project_packages()
        # Unused snapshots are removed when enabled in Snapshots section.
        SnapshotManager.shared().start_auto_clean()

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
        # Virtual machines started automatically are stopped with app.
        from .repository import Repository
        for machine in Repository.BuildMachine.value:
            try:
                machine.stop_if_started_automatically()
            except Exception as e:
                print(f"Failed to stop virtual machine {machine.name}: {e}")
        Gio.Application.do_shutdown(self)

    def on_about_action(self, widget, _):
        """Callback for the app.about action."""
        about = Adw.AboutWindow(transient_for=self.props.active_window,
                                application_name='catalystlab',
                                application_icon='com.damiandudycz.CatalystLab',
                                developer_name='Unknown',
                                version=app_info.APP_VERSION,
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
    app_info.set_app_version(version)
    app = CatalystlabApplication()
    return app.run(sys.argv)
