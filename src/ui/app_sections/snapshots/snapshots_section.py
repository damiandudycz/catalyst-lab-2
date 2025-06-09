from gi.repository import Gtk, Adw
from .app_section import app_section
from .snapshot_details_view import SnapshotDetailsView
from .snapshot_create_view import SnapshotCreateView
from .app_events import app_event_bus, AppEvents

@app_section(title="Snapshots", icon="video-frame-svgrepo-com-symbolic", order=4_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/snapshots/snapshots_section.ui')
class SnapshotsSection(Gtk.Box):
    __gtype_name__ = "SnapshotsSection"

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view

    @Gtk.Template.Callback()
    def on_item_row_pressed(self, sender, item):
        self.content_navigation_view.push_view(SnapshotDetailsView(snapshot=item) , title="Snapshot details")

    @Gtk.Template.Callback()
    def on_installation_row_pressed(self, sender, installation):
        app_event_bus.emit(AppEvents.PRESENT_VIEW, SnapshotCreateView(installation_in_progress=installation), "New toolset", 640, 480)

    @Gtk.Template.Callback()
    def on_add_new_item_pressed(self, sender):
        app_event_bus.emit(AppEvents.PRESENT_VIEW, SnapshotCreateView(), "New snapshot", 640, 480)

