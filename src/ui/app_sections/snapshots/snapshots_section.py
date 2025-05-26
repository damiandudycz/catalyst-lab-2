from gi.repository import Gtk, GObject
from gi.repository import Adw
from .app_section import app_section
from .snapshot_manager import SnapshotManager
from .app_events import app_event_bus, AppEvents
from .snapshot_create_view import SnapshotCreateView
from .snapshot_manager import Snapshot
from .repository import Repository, RepositoryEvent

class SnapshotInstallation:
    started_installations = []

@app_section(title="Snapshots", icon="video-frame-svgrepo-com-symbolic", order=3_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/snapshots/snapshots_section.ui')
class SnapshotsSection(Gtk.Box):
    __gtype_name__ = "SnapshotsSection"

    snapshots_container = Gtk.Template.Child()

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        self.wizard_mode = kwargs.pop('wizard_mode', False)
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view
        # Setup snapshots entries
        self._load_snapshots()
        # Subscribe to relevant events
        Repository.SNAPSHOTS.event_bus.subscribe(RepositoryEvent.VALUE_CHANGED, self.snapshots_updated)

    @Gtk.Template.Callback()
    def on_add_snapshot_activated(self, button):
        if self.wizard_mode:
            self.content_navigation_view.push_view(SnapshotCreateView(), title="New snapshot")
        else:
            app_event_bus.emit(AppEvents.PRESENT_VIEW, SnapshotCreateView(), "New snapshot", 640, 480)

    def snapshots_updated(self, _):
        self._load_snapshots()

    def _load_snapshots(self, started_installations: list[SnapshotInstallation] = SnapshotInstallation.started_installations):
        # Remove previously added rows
        if hasattr(self, "_snapshot_rows"):
            for row in self._snapshot_rows:
                self.snapshots_container.remove(row)
        self._snapshot_rows = []

        # Refresh the list
        snapshots = Repository.SNAPSHOTS.value

        for snapshot in snapshots:
            snapshot_row = SnapshotRow(snapshot=snapshot)
#            snapshot_row.connect("activated", self.on_snapshot_row_pressed)
            self.snapshots_container.insert(snapshot_row, 0)
            self._snapshot_rows.append(snapshot_row)

#        for installation in started_installations:
#            installation_row = ToolsetInstallationRow(installation=installation)
#            installation_row.connect("activated", self.on_installation_row_pressed)
#            self.external_toolsets_container.insert(installation_row, 0)
#            self._snapshot_rows.append(installation_row)

class SnapshotRow(Adw.ActionRow):

    def __init__(self, snapshot: Snapshot):
        super().__init__(title=snapshot.filename, icon_name="video-frame-svgrepo-com-symbolic")
        self.snapshot = snapshot
        # Status indicator
#        self.status_indicator = StatusIndicator()
#        self.status_indicator.set_margin_start(6)
#        self.status_indicator.set_margin_end(6)
#        self.add_suffix(self.status_indicator)
        # Make subtitle from installed app versions
#        app_strings: [str] = []
#        for app in ToolsetApplication.ALL:
#            if app.auto_select:
#                continue
#            version = toolset.get_installed_app_version(app)
#            if version is not None:
#                app_strings.append(f"{app.name}: {version}")
#        if app_strings:
#            self.set_subtitle(", ".join(app_strings))
#        else:
#            self.set_subtitle("")
        self.set_activatable(True)
#        self._setup_status_indicator()
        # events
#        toolset.event_bus.subscribe(
#            ToolsetEvents.SPAWNED_CHANGED,
#            self._setup_status_indicator
#        )
#        toolset.event_bus.subscribe(
#            ToolsetEvents.IN_USE_CHANGED,
#            self._setup_status_indicator
#        )

