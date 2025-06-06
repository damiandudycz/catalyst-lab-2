from gi.repository import Gtk, GObject
from gi.repository import Adw
from .app_section import AppSection, app_section
from .runtime_env import RuntimeEnv
from .toolset import ToolsetEnv, Toolset, ToolsetEvents
from .toolset_env_builder import ToolsetEnvBuilder
from .toolset import Toolset
from .toolset_details_view import ToolsetDetailsView
from .multistage_process import MultiStageProcess, MultiStageProcessEvent, MultiStageProcessState
from .toolset_installation import ToolsetInstallation
from .toolset_application import ToolsetApplication
from .hotfix_patching import HotFix
from .root_function import root_function
from .root_helper_client import RootHelperClient, ServerCall, AuthorizationKeeper
from .root_helper_server import ServerCommand
from .toolset_create_view import ToolsetCreateView
from .app_events import app_event_bus, AppEvents
from .status_indicator import StatusIndicator, StatusIndicatorState
import time
from .repository import Serializable, Repository, RepositoryEvent
from typing import Any

@app_section(title="Environments", icon="preferences-other-symbolic", order=2_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/environments/environments_section.ui')
class EnvironmentsSection(Gtk.Box):
    __gtype_name__ = "EnvironmentsSection"

    toolset_system = Gtk.Template.Child()
    toolset_system_checkbox = Gtk.Template.Child()
    toolset_system_validate_button = Gtk.Template.Child()
    toolset_add_button = Gtk.Template.Child()
    external_toolsets_container = Gtk.Template.Child()

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        self.wizard_mode = kwargs.pop('wizard_mode', False)
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view
        self._ignore_toolset_checkbox_signal = False
        # Setup host env entry
        self._load_system_toolset()
        # Setup external env entries
        self._load_external_toolsets()
        # Subscribe to relevant events
        Repository.Toolset.event_bus.subscribe(RepositoryEvent.VALUE_CHANGED, self.toolsets_updated)
        MultiStageProcess.event_bus.subscribe(MultiStageProcessEvent.STARTED_PROCESSES_CHANGED, self.toolsets_installations_updated)

    def toolsets_updated(self, _):
        self._load_system_toolset()
        self._load_external_toolsets()

    def toolsets_installations_updated(self, process_class: type[MultiStageProcess], started_processes: list[MultiStageProcess]):
        if issubclass(process_class, ToolsetInstallation):
            self._load_external_toolsets(started_processes=started_processes)

    def _load_system_toolset(self):
        if ToolsetEnv.SYSTEM.is_allowed_in_current_host():
            system_toolset_selected = any(toolset.env == ToolsetEnv.SYSTEM for toolset in Repository.Toolset.value)
            self._set_toolset_system_checkbox_active(system_toolset_selected)
            self.toolset_system_validate_button.set_visible(system_toolset_selected)
        else:
            self.toolset_system.set_sensitive(False)
            self._set_toolset_system_checkbox_active(False)
            self.toolset_system_validate_button.set_visible(False)

    def _load_external_toolsets(self, started_processes: list[MultiStageProcess] | None = None):
        if started_processes is None:
            started_processes = MultiStageProcess.get_started_processes_by_class(ToolsetInstallation)
        # Remove previously added toolset rows
        if hasattr(self, "_external_toolset_rows"):
            for row in self._external_toolset_rows:
                self.external_toolsets_container.remove(row)
        self._external_toolset_rows = []

        # Refresh the list
        external_toolsets = [
            toolset for toolset in Repository.Toolset.value
            if toolset.env == ToolsetEnv.EXTERNAL
        ]

        for toolset in external_toolsets:
            toolset_row = ToolsetRow(toolset=toolset)
            toolset_row.connect("activated", self.on_external_toolset_row_pressed)
            toolset_row.set_activatable(True)
            icon = Gtk.Image.new_from_icon_name("go-next-symbolic")
            icon.add_css_class("dimmed")
            toolset_row.add_suffix(icon)
            self.external_toolsets_container.insert(toolset_row, 0)
            self._external_toolset_rows.append(toolset_row)

        for installation in started_processes:
            installation_row = ToolsetInstallationRow(installation=installation)
            installation_row.connect("activated", self.on_installation_row_pressed)
            self.external_toolsets_container.insert(installation_row, 0)
            self._external_toolset_rows.append(installation_row)

    def on_external_toolset_row_pressed(self, sender):
        self.content_navigation_view.push_view(ToolsetDetailsView(toolset=sender.toolset), title="Toolset details")
        #app_event_bus.emit(AppEvents.PUSH_VIEW, ToolsetDetailsView(toolset=sender.toolset), "Toolset details")
        #app_event_bus.emit(AppEvents.PRESENT_VIEW, ToolsetDetailsView(toolset=sender.toolset), "Toolset details", 640, 480)

    def on_installation_row_pressed(self, sender):
        installation = getattr(sender, "installation", None)
        if installation is None:
            return
        if self.wizard_mode:
            self.content_navigation_view.push_view(ToolsetCreateView(installation_in_progress=installation), title="New toolset")
        else:
            app_event_bus.emit(AppEvents.PRESENT_VIEW, ToolsetCreateView(installation_in_progress=installation), "New toolset", 640, 480)

    # Sets the state without calling a callback.
    def _set_toolset_system_checkbox_active(self, active: bool):
        self._ignore_toolset_checkbox_signal = True
        self.toolset_system_checkbox.set_active(active)
        self._ignore_toolset_checkbox_signal = False

    @Gtk.Template.Callback()
    def toolset_system_checkbox_toggled(self, checkbox):
        if self._ignore_toolset_checkbox_signal:
            return
        # If checkbox is checked, place it at the start of the list
        if checkbox.get_active():
            system_toolset = Toolset.create_system()
            Repository.Toolset.value.append(system_toolset)
        elif (ts := next((ts for ts in Repository.Toolset.value if ts.env == ToolsetEnv.SYSTEM), None)):
            Repository.Toolset.value.remove(ts)

    @Gtk.Template.Callback()
    def on_add_toolset_activated(self, button):
        if self.wizard_mode:
            self.content_navigation_view.push_view(ToolsetCreateView(), title="New toolset")
        else:
            app_event_bus.emit(AppEvents.PRESENT_VIEW, ToolsetCreateView(), "New toolset", 640, 480)

    @Gtk.Template.Callback()
    def on_validate_system_toolset_pressed(self, button):
        # Testing only
        system_toolset = next(
            (toolset for toolset in Repository.Toolset.value if toolset.env == ToolsetEnv.SYSTEM),
            None  # default if no match found
        )
        if not system_toolset:
            raise RuntimeError("System host env not available")
        if not system_toolset.spawned:
            if not system_toolset.reserve():
                raise RuntimeError("Failed to reserve toolset")
            system_toolset.spawn()
        system_toolset.analyze()

class ToolsetRow(Adw.ActionRow):

    def __init__(self, toolset: Toolset):
        super().__init__(title=toolset.name, icon_name="preferences-other-symbolic")
        self.toolset = toolset
        # Status indicator
        self.status_indicator = StatusIndicator()
        self.status_indicator.set_margin_start(6)
        self.status_indicator.set_margin_end(6)
        self.add_suffix(self.status_indicator)
        # Make subtitle from installed app versions
        app_strings: [str] = []
        for app in ToolsetApplication.ALL:
            if app.auto_select:
                continue
            app_install = toolset.get_app_install(app=app)
            if app_install:
                app_strings.append(f"{app.name}: {app_install.version}")
        if app_strings:
            self.set_subtitle(", ".join(app_strings))
        else:
            self.set_subtitle("")
        self.set_activatable(True)
        self._setup_status_indicator()
        # events
        toolset.event_bus.subscribe(
            ToolsetEvents.SPAWNED_CHANGED,
            self._setup_status_indicator
        )
        toolset.event_bus.subscribe(
            ToolsetEvents.IN_USE_CHANGED,
            self._setup_status_indicator
        )
        toolset.event_bus.subscribe(
            ToolsetEvents.IS_RESERVED_CHANGED,
            self._setup_status_indicator
        )

    def _setup_status_indicator(self, data: Any | None = None):
        self.status_indicator.set_blinking(self.toolset.in_use)
        if self.toolset.is_reserved:
            self.status_indicator.set_state(StatusIndicatorState.ENABLED_UNSAFE)
        elif self.toolset.spawned and self.toolset.store_changes:
            self.status_indicator.set_state(StatusIndicatorState.ENABLED_UNSAFE)
        elif self.toolset.spawned:
            self.status_indicator.set_state(StatusIndicatorState.ENABLED)
        else:
            self.status_indicator.set_state(StatusIndicatorState.DISABLED)

class ToolsetInstallationRow(Adw.ActionRow):

    def __init__(self, installation: ToolsetInstallation):
        super().__init__(
            title=installation.name(),
            icon_name="preferences-other-symbolic"
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
            MultiStageProcessState.SETUP: (False, "", "", "Preparing installation"),
            MultiStageProcessState.IN_PROGRESS: (False, "", "", "Installation in progress"),
            MultiStageProcessState.FAILED: (True, "error-box-svgrepo-com-symbolic", "error", "Installation failed"),
            MultiStageProcessState.COMPLETED: (True, "check-square-svgrepo-com-symbolic", "success", "Installation completed"),
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

