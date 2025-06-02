from __future__ import annotations
from gi.repository import Gtk, GLib, Gio, Adw

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/releng_create/releng_create_view.ui')
class RelengCreateView(Gtk.Box):
    __gtype_name__ = "RelengCreateView"

    # Main views:
    setup_view = Gtk.Template.Child()
    install_view = Gtk.Template.Child()
    # Setup view elements:
    carousel = Gtk.Template.Child()

    def __init__(self, installation_in_progress: RelengInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        # ...
        self.connect("map", self.on_map)

    def on_map(self, widget):
        self.install_view.content_navigation_view = self.content_navigation_view
        self.install_view._window = self._window

