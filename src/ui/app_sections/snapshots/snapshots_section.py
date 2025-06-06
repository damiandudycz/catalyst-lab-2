from gi.repository import Gtk, GObject
from gi.repository import Adw
from .app_section import app_section
from .snapshot_manager import SnapshotManager
from .app_events import app_event_bus, AppEvents
from .snapshot_create_view import SnapshotCreateView
from .snapshot import Snapshot
from .repository import Repository, RepositoryEvent
from .snapshot_installation import SnapshotInstallation
from .multistage_process import MultiStageProcess, MultiStageProcessEvent, MultiStageProcessState
from .snapshot_details_view import SnapshotDetailsView

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
        Repository.Snapshot.event_bus.subscribe(RepositoryEvent.VALUE_CHANGED, self.snapshots_updated)
        MultiStageProcess.event_bus.subscribe(MultiStageProcessEvent.STARTED_PROCESSES_CHANGED, self.snapshot_installations_updated)

    def snapshot_installations_updated(self, process_class: type[MultiStageProcess], started_processes: list[MultiStageProcess]):
        if issubclass(process_class, SnapshotInstallation):
            self._load_snapshots(started_processes=started_processes)

    @Gtk.Template.Callback()
    def on_add_snapshot_activated(self, button):
        if self.wizard_mode:
            self.content_navigation_view.push_view(SnapshotCreateView(), title="New snapshot")
        else:
            app_event_bus.emit(AppEvents.PRESENT_VIEW, SnapshotCreateView(), "New snapshot", 640, 480)

    def snapshots_updated(self, _):
        self._load_snapshots()

    def _load_snapshots(self, started_processes: list[MultiStageProcess] | None = None):
        if started_processes is None:
            started_processes = MultiStageProcess.get_started_processes_by_class(SnapshotInstallation)
        # Remove previously added rows
        if hasattr(self, "_snapshot_rows"):
            for row in self._snapshot_rows:
                self.snapshots_container.remove(row)
        self._snapshot_rows = []

        # Refresh the list
        snapshots = Repository.Snapshot.value

        for snapshot in snapshots:
            snapshot_row = SnapshotRow(snapshot=snapshot)
            snapshot_row.set_activatable(True)
            icon = Gtk.Image.new_from_icon_name("go-next-symbolic")
            icon.add_css_class("dimmed")
            snapshot_row.add_suffix(icon)
            snapshot_row.connect("activated", self.on_snapshot_row_pressed)
            self.snapshots_container.insert(snapshot_row, 0)
            self._snapshot_rows.append(snapshot_row)

        for installation in started_processes:
            installation_row = SnapshotInstallationRow(installation=installation)
            installation_row.connect("activated", self.on_installation_row_pressed)
            self.snapshots_container.insert(installation_row, 0)
            self._snapshot_rows.append(installation_row)

    def on_snapshot_row_pressed(self, sender):
        self.content_navigation_view.push_view(SnapshotDetailsView(snapshot=sender.snapshot) , title="Snapshot details")

    def on_installation_row_pressed(self, sender):
        installation = getattr(sender, "installation", None)
        if installation is None:
            return
        if self.wizard_mode:
            self.content_navigation_view.push_view(SnapshotCreateView(installation_in_progress=installation), title="New toolset")
        else:
            app_event_bus.emit(AppEvents.PRESENT_VIEW, SnapshotCreateView(installation_in_progress=installation), "New toolset", 640, 480)

class SnapshotRow(Adw.ActionRow):

    @classmethod
    def extract_snapshot_id(cls, filename: str) -> str:
        return filename.rsplit('.', 1)[0]

    def __init__(self, snapshot: Snapshot):
        super().__init__(
            title=snapshot.date.strftime("%Y-%d-%m %H:%M"),
            subtitle=SnapshotRow.extract_snapshot_id(snapshot.filename),
            icon_name="video-frame-svgrepo-com-symbolic"
        )
        self.snapshot = snapshot

class SnapshotInstallationRow(Adw.ActionRow):

    def __init__(self, installation: SnapshotInstallation):
        super().__init__(
            title=installation.name(),
            icon_name="video-frame-svgrepo-com-symbolic"
        )
        self.installation = installation
        self.set_activatable(True)
        self.progress_label = Gtk.Label()
        self.progress_label.add_css_class("dim-label")
        self.progress_label.add_css_class("caption")
        self.add_suffix(self.progress_label)
        self._set_status(status=installation.status)
        self._set_progress_label(installation.progress)
        installation.event_bus.subscribe(
            MultiStageProcessEvent.STATE_CHANGED,
            self._set_status
        )
        installation.event_bus.subscribe(
            MultiStageProcessEvent.PROGRESS_CHANGED,
            self._set_progress_label
        )

    def _set_progress_label(self, progress):
        self.progress_label.set_label(f"{int(progress * 100)}%")

    def _set_status(self, status: MultiStageProcessState):
        if not hasattr(self, "status_icon"):
            self.status_icon = Gtk.Image()
            self.status_icon.set_pixel_size(24)
            self.add_suffix(self.status_icon)
        status_props = {
            MultiStageProcessState.SETUP: (False, "", "", "Preparing fetching"),
            MultiStageProcessState.IN_PROGRESS: (False, "", "", "Fetching in progress"),
            MultiStageProcessState.FAILED: (True, "error-box-svgrepo-com-symbolic", "error", "Fetching failed"),
            MultiStageProcessState.COMPLETED: (True, "check-square-svgrepo-com-symbolic", "success", "Fetching completed"),
        }
        visible, icon_name, style, subtitle = status_props[status]
        self.progress_label.set_visible(not visible)
        self.status_icon.set_visible(visible)
        self.status_icon.set_from_icon_name(icon_name)
        self.set_subtitle(subtitle)
        if hasattr(self.status_icon, 'used_css_class'):
            self.status_icon.remove_css_class(self.used_css_class)
        if style:
            self.status_icon.used_css_class = style
            self.status_icon.add_css_class(style)

