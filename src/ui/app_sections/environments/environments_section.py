from gi.repository import Gtk, GObject
from gi.repository import Adw
from .app_section import AppSection, app_section
from .runtime_env import RuntimeEnv
from .toolset import ToolsetEnv, Toolset, ToolsetEvents
from .toolset_env_builder import ToolsetEnvBuilder
from .toolset import Toolset, ToolsetInstallation, ToolsetInstallationEvent, ToolsetInstallationStage, ToolsetApplication
from .hotfix_patching import HotFix
from .root_function import root_function
from .root_helper_client import RootHelperClient
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
        Repository.TOOLSETS.event_bus.subscribe(RepositoryEvent.VALUE_CHANGED, self.toolsets_updated)
        ToolsetInstallation.event_bus.subscribe(ToolsetInstallationEvent.STARTED_INSTALLATIONS_CHANGED, self.toolsets_installations_updated)

    def toolsets_updated(self, _):
        self._load_system_toolset()
        self._load_external_toolsets()

    def toolsets_installations_updated(self, started_installations: list[ToolsetInstallation] = ToolsetInstallation.started_installations):
        self._load_external_toolsets(started_installations=started_installations)

    def _load_system_toolset(self):
        if ToolsetEnv.SYSTEM.is_allowed_in_current_host():
            system_toolset_selected = any(toolset.env == ToolsetEnv.SYSTEM for toolset in Repository.TOOLSETS.value)
            self._set_toolset_system_checkbox_active(system_toolset_selected)
            self.toolset_system_validate_button.set_visible(system_toolset_selected)
        else:
            self.toolset_system.set_sensitive(False)
            self._set_toolset_system_checkbox_active(False)
            self.toolset_system_validate_button.set_visible(False)

    def _load_external_toolsets(self, started_installations: list[ToolsetInstallation] = ToolsetInstallation.started_installations):
        # Remove previously added toolset rows
        if hasattr(self, "_external_toolset_rows"):
            for row in self._external_toolset_rows:
                self.external_toolsets_container.remove(row)
        self._external_toolset_rows = []

        # Refresh the list
        external_toolsets = [
            toolset for toolset in Repository.TOOLSETS.value
            if toolset.env == ToolsetEnv.EXTERNAL
        ]

        for toolset in external_toolsets:
            toolset_row = ToolsetRow(toolset=toolset)
            toolset_row.connect("activated", self.on_external_toolset_row_pressed)
            self.external_toolsets_container.insert(toolset_row, 0)
            self._external_toolset_rows.append(toolset_row)

        for installation in started_installations:
            installation_row = ToolsetInstallationRow(installation=installation)
            installation_row.connect("activated", self.on_installation_row_pressed)
            self.external_toolsets_container.insert(installation_row, 0)
            self._external_toolset_rows.append(installation_row)

    def on_external_toolset_row_pressed(self, sender):
        def worker(authorized: bool):
            if not authorized:
                return
            try:
                if not sender.toolset.spawned:
                    sender.toolset.spawn()
                    #sender.toolset.analyze()
                    sender.toolset.run_command(command="emerge --sync")
                else:
                    sender.toolset.unspawn()
            except Exception as e:
                print(e)
        RootHelperClient.shared().authorize_and_run(callback=worker)

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
            Repository.TOOLSETS.value.append(system_toolset)
        elif (ts := next((ts for ts in Repository.TOOLSETS.value if ts.env == ToolsetEnv.SYSTEM), None)):
            Repository.TOOLSETS.value.remove(ts)

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
            (toolset for toolset in Repository.TOOLSETS.value if toolset.env == ToolsetEnv.SYSTEM),
            None  # default if no match found
        )
        if not system_toolset:
            raise RuntimeError("System host env not available")
        if not system_toolset.spawned:
            system_toolset.spawn()
        system_toolset.analyze()

class ToolsetRow(Adw.ActionRow):

    def __init__(self, toolset: Toolset):
        super().__init__(title=toolset.name, subtitle="Installation in progress", icon_name="preferences-other-symbolic")
        self.toolset = toolset
        # Status indicator
        self.status_indicator = StatusIndicator()
        self.add_suffix(self.status_indicator)
        # Make subtitle from installed app versions
        app_strings: [str] = []
        for app in ToolsetApplication.ALL:
            if app.auto_select:
                continue
            version = toolset.get_installed_app_version(app)
            if version is not None:
                app_strings.append(f"{app.name}: {version}")
        qemu_metadata: dict[str, Any] = toolset.metadata.get(ToolsetApplication.QEMU.package, {})
        qemu_interpreters_metadata: list[str] = qemu_metadata.get("interpreters", [])
        if qemu_interpreters_metadata:
            app_strings.append(f"Interpreters: {len(qemu_interpreters_metadata)}")
        if app_strings:
            self.set_subtitle(", ".join(app_strings))
        self.toolset = toolset
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

    def _setup_status_indicator(self, data: Any | None = None):
        self.status_indicator.set_blinking(self.toolset.in_use)
        if self.toolset.spawned and self.toolset.store_changes:
            self.status_indicator.set_state(StatusIndicatorState.ENABLED_UNSAFE)
        elif self.toolset.spawned:
            self.status_indicator.set_state(StatusIndicatorState.ENABLED)
        else:
            self.status_indicator.set_state(StatusIndicatorState.DISABLED)

class ToolsetInstallationRow(Adw.ActionRow):

    def __init__(self, installation: ToolsetInstallation):
        super().__init__(title=installation.name(), subtitle="Installation in progress")
        self.installation = installation
        self.set_activatable(True)
        self.status_label = Gtk.Label()
        self.status_label.add_css_class("dim-label")
        self.status_label.add_css_class("caption")
        self.add_suffix(self.status_label)
        self._set_status_icon(status=installation.status)
        self._setup_progress_label(progress=installation.progress)
        installation.event_bus.subscribe(
            ToolsetInstallationEvent.STATE_CHANGED,
            self._set_status_icon
        )
        installation.event_bus.subscribe(
            ToolsetInstallationEvent.PROGRESS_CHANGED,
            self._setup_progress_label
        )

    def _setup_progress_label(self, progress: float):
        self.status_label.set_label(f"{int(progress * 100)}%")

    def _set_status_icon(self, status: ToolsetInstallationStage):
        if not hasattr(self, "status_icon"):
            self.status_icon = Gtk.Image()
            self.status_icon.set_pixel_size(24)
            self.add_prefix(self.status_icon)
        icon_name = {
            ToolsetInstallationStage.SETUP: "square-alt-arrow-right-svgrepo-com-symbolic",
            ToolsetInstallationStage.INSTALL: "menu-dots-square-svgrepo-com-symbolic",
            ToolsetInstallationStage.FAILED: "error-box-svgrepo-com-symbolic",
            ToolsetInstallationStage.COMPLETED: "check-square-svgrepo-com-symbolic"
        }.get(status)
        styles = {
            ToolsetInstallationStage.SETUP: "dimmed",
            ToolsetInstallationStage.INSTALL: "",
            ToolsetInstallationStage.FAILED: "error",
            ToolsetInstallationStage.COMPLETED: "success"
        }
        style = styles.get(status)
        self.status_icon.set_from_icon_name(icon_name)
        for css_class in styles.values():
            if css_class:
                self.status_icon.remove_css_class(css_class)
        if style:
            self.status_icon.add_css_class(style)
