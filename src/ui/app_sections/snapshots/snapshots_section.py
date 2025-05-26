from gi.repository import Gtk
from gi.repository import Adw
from .app_section import app_section
from .snapshot_manager import SnapshotManager

@app_section(title="Snapshots", icon="video-frame-svgrepo-com-symbolic", order=3_000)
#@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/snapshots/snapshots_section.ui')
class SnapshotsSection(Gtk.Box):
    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        snapshot_manager = SnapshotManager.shared()
        print(snapshot_manager.snapshots)

