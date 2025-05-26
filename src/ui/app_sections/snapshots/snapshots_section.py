from gi.repository import Gtk, GObject
from gi.repository import Adw
from .app_section import app_section
from .snapshot_manager import SnapshotManager
from .app_events import app_event_bus, AppEvents
from .snapshot_create_view import SnapshotCreateView

@app_section(title="Snapshots", icon="video-frame-svgrepo-com-symbolic", order=3_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/snapshots/snapshots_section.ui')
class SnapshotsSection(Gtk.Box):
    __gtype_name__ = "SnapshotsSection"

    snapshots_container = Gtk.Template.Child()

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        self.wizard_mode = kwargs.pop('wizard_mode', False)
        super().__init__(**kwargs)
        snapshot_manager = SnapshotManager.shared()
        print(snapshot_manager.snapshots)

    @Gtk.Template.Callback()
    def on_add_snapshot_activated(self, button):
        if self.wizard_mode:
            self.content_navigation_view.push_view(SnapshotCreateView(), title="New snapshot")
        else:
            app_event_bus.emit(AppEvents.PRESENT_VIEW, SnapshotCreateView(), "New snapshot", 640, 480)

