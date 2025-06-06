from gi.repository import Gtk
from gi.repository import Adw
from .app_section import app_section
from .releng_create_view import RelengCreateView
from .app_events import app_event_bus, AppEvents
from .multistage_process import MultiStageProcess, MultiStageProcessEvent, MultiStageProcessState
from .releng_installation import RelengInstallation
from .repository import Repository, RepositoryEvent
from .releng_directory import RelengDirectory, RelengDirectoryStatus, RelengDirectoryEvent
from .status_indicator import StatusIndicator, StatusIndicatorState
from .releng_details_view import RelengDetailsView
from typing import Any
from datetime import datetime

@app_section(title="Releng", icon="book-minimalistic-svgrepo-com-symbolic", order=4_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/releng/releng_section.ui')
class RelengSection(Gtk.Box):
    __gtype_name__ = "RelengSection"

    releng_directories_container = Gtk.Template.Child()

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        self.wizard_mode = kwargs.pop('wizard_mode', False)
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view
        # Setup releng entries
        self._load_releng_directories()
        # Subscribe to relevant events
        Repository.RelengDirectory.event_bus.subscribe(RepositoryEvent.VALUE_CHANGED, self.releng_updated)
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
        releng_directories = Repository.RelengDirectory.value

        for releng_directory in releng_directories:
            releng_directory_row = RelengDirectoryRow(releng_directory=releng_directory)
            releng_directory_row.connect("activated", self.on_releng_directory_row_pressed)
            releng_directory_row.set_activatable(True)
            icon = Gtk.Image.new_from_icon_name("go-next-symbolic")
            icon.add_css_class("dimmed")
            releng_directory_row.add_suffix(icon)
            self.releng_directories_container.insert(releng_directory_row, 0)
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
        self.content_navigation_view.push_view(RelengDetailsView(releng_directory=sender.releng_directory) , title="Releng directory details")

    def on_installation_row_pressed(self, sender):
        installation = getattr(sender, "installation", None)
        if installation is None:
            return
        if self.wizard_mode:
            self.content_navigation_view.push_view(RelengCreateView(installation_in_progress=installation), title="New releng directory")
        else:
            app_event_bus.emit(AppEvents.PRESENT_VIEW, RelengCreateView(installation_in_progress=installation), "New releng directory", 640, 480)

class RelengDirectoryRow(Adw.ActionRow):

    def __init__(self, releng_directory: RelengDirectory):
        super().__init__(title=releng_directory.name, icon_name="book-minimalistic-svgrepo-com-symbolic")
        self.releng_directory = releng_directory
        # Status indicator
        self.status_indicator = StatusIndicator()
        self.status_indicator.set_margin_start(6)
        self.status_indicator.set_margin_end(6)
        self.add_suffix(self.status_indicator)
        self._setup_status_indicator()
        # events
        releng_directory.event_bus.subscribe(
            RelengDirectoryEvent.STATUS_CHANGED,
            self._setup_status_indicator
        )

    def _setup_status_indicator(self, data: Any | None = None):
        if self.releng_directory.status == RelengDirectoryStatus.UNKNOWN:
            self.status_indicator.set_state(StatusIndicatorState.DISABLED)
        elif self.releng_directory.status == RelengDirectoryStatus.UNCHANGED:
            self.status_indicator.set_state(StatusIndicatorState.ENABLED)
        elif self.releng_directory.status == RelengDirectoryStatus.CHANGED:
            self.status_indicator.set_state(StatusIndicatorState.ENABLED_UNSAFE)
        else:
            self.status_indicator.set_state(StatusIndicatorState.DISABLED)
        self.update_subtitle()

    def update_subtitle(self):
        self.set_subtitle(f"{self.releng_directory.branch_name}, {self.releng_directory.last_commit_date.strftime('%Y-%d-%m %H:%M') if self.releng_directory.last_commit_date else 'Unknown date'}")

class RelengInstallationRow(Adw.ActionRow):

    def __init__(self, installation: RelengInstallation):
        super().__init__(title=installation.name(), icon_name="book-minimalistic-svgrepo-com-symbolic")
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

