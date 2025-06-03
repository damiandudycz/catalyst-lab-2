from gi.repository import Gtk
from gi.repository import Adw
from .app_section import app_section
from .releng_create_view import RelengCreateView
from .app_events import app_event_bus, AppEvents
from .multistage_process import MultiStageProcess, MultiStageProcessEvent, MultiStageProcessState
from .releng_installation import RelengInstallation
from .repository import Repository, RepositoryEvent

@app_section(title="Releng", icon="book-minimalistic-svgrepo-com-symbolic", order=4_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/releng/releng_section.ui')
class RelengSection(Gtk.Box):
    __gtype_name__ = "RelengSection"

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        self.wizard_mode = kwargs.pop('wizard_mode', False)
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view
        # Setup releng entries
        self._load_releng_directories()
        # Subscribe to relevant events
        Repository.RELENG.event_bus.subscribe(RepositoryEvent.VALUE_CHANGED, self.releng_updated)
        MultiStageProcess.event_bus.subscribe(MultiStageProcessEvent.STARTED_PROCESSES_CHANGED, self.releng_installations_updated)

    def releng_installations_updated(self, process_class: type[MultiStageProcess], started_processes: list[MultiStageProcess]):
        if issubclass(process_class, RelengInstallation):
            self._load_releng_directories(started_processes=started_processes)

    def releng_updated(self, _):
        self._load_releng_directories()

    def _load_releng_directories(self, started_processes: list[MultiStageProcess] | None = None):
        if started_processes is None:
            started_processes = MultiStageProcess.get_started_processes_by_class(RelengInstallation)
        # Remove previously added rows
        if hasattr(self, "_releng_rows"):
            for row in self._releng_rows:
                self.releng_directories_container.remove(row)
        self._releng_rows = []

        # Refresh the list
        releng_directories = Repository.RELENG.value

        for releng_directory in releng_directories:
            releng_directory_row = RelengDirectoryRow(releng_directory=releng_directory)
            releng_directory_row.connect("activated", self.on_releng_directory_row_pressed)
            self.snapshots_container.insert(releng_directory_row, 0)
            self._releng_rows.append(releng_directory_row)

        for installation in started_processes:
            installation_row = RelengInstallationRow(installation=installation)
            installation_row.connect("activated", self.on_installation_row_pressed)
            self.releng_directories_container.insert(installation_row, 0)
            self._releng_rows.append(installation_row)

    @Gtk.Template.Callback()
    def on_add_releng_activated(self, sender):
        if self.wizard_mode:
            self.content_navigation_view.push_view(RelengCreateView(), title="New releng directory")
        else:
            app_event_bus.emit(AppEvents.PRESENT_VIEW, RelengCreateView(), "New releng directory", 640, 480)

    def on_releng_directory_row_pressed(self, sender):
        pass

