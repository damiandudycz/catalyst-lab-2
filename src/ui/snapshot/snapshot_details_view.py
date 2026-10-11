from gi.repository import Gtk, Adw
from .snapshot_manager import SnapshotManager
from .snapshot import Snapshot
from .packages_browser_view import PackagesBrowserView

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/snapshot/snapshot_details_view.ui')
class SnapshotDetailsView(Gtk.Box):
    __gtype_name__ = "SnapshotDetailsView"

    browser_box = Gtk.Template.Child()

    def __init__(self, snapshot: Snapshot, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.snapshot = snapshot
        self.content_navigation_view = content_navigation_view
        self.browser = PackagesBrowserView(loader=snapshot.load_ebuilds, loading_title="Loading snapshot…")
        self.browser_box.append(self.browser)
        self.browser.load()

    @Gtk.Template.Callback()
    def button_delete_clicked(self, sender):
        SnapshotManager.shared().remove_snapshot(self.snapshot)
        if hasattr(self, "_window"):
            self._window.close()
        elif hasattr(self, "content_navigation_view"):
            self.content_navigation_view.pop()
