from gi.repository import Gtk, GObject
from gi.repository import Adw
from .app_section import AppSection, app_section
from .runtime_env import RuntimeEnv
from .toolset import ToolsetEnv, Toolset
from .settings import Settings, SettingsEvents
from .toolset_env_builder import ToolsetEnvBuilder
from .toolset import Toolset, ToolsetInstallation, ToolsetInstallationEvent, ToolsetInstallationStage
from .hotfix_patching import HotFix
from .root_function import root_function
from .root_helper_client import RootHelperClient
from .root_helper_server import ServerCommand
from .toolset_create_view import ToolsetCreateView
from .app_events import app_event_bus, AppEvents
import time

@app_section(title="Environments", icon="preferences-other-symbolic", order=2_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/environments/environments_section.ui')
class EnvironmentsSection(Gtk.Box):
    __gtype_name__ = "EnvironmentsSection"

    toolset_system = Gtk.Template.Child()
    toolset_system_checkbox = Gtk.Template.Child()
    toolset_system_validate_button = Gtk.Template.Child()
    toolset_add_button = Gtk.Template.Child()
    toolset_installations_list = Gtk.Template.Child()
    external_toolsets_container = Gtk.Template.Child()

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view
        self._ignore_toolset_checkbox_signal = False
        # Setup host env entry
        self._load_system_toolset()
        # Setup external env entries
        self._load_external_toolsets()
        # Setup ongoing installations
        self._load_installations()
        # Subscribe to relevant events
        Settings.current().event_bus.subscribe(SettingsEvents.TOOLSETS_CHANGED, self.toolsets_updated)
        ToolsetInstallation.event_bus.subscribe(ToolsetInstallationEvent.STARTED_INSTALLATIONS_CHANGED, self.toolsets_installations_updated)

    def toolsets_updated(self):
        self._load_system_toolset()
        self._load_external_toolsets()

    def toolsets_installations_updated(self, started_installations: list[ToolsetInstallation] = ToolsetInstallation.started_installations):
        self._load_installations(started_installations=started_installations)

    def _load_system_toolset(self):
        if ToolsetEnv.SYSTEM.is_allowed_in_current_host():
            system_toolset_selected = any(toolset.env == ToolsetEnv.SYSTEM for toolset in Settings.current().get_toolsets())
            self._set_toolset_system_checkbox_active(system_toolset_selected)
            self.toolset_system_validate_button.set_visible(system_toolset_selected)
        else:
            self.toolset_system.set_sensitive(False)
            self._set_toolset_system_checkbox_active(False)
            self.toolset_system_validate_button.set_visible(False)

    def _load_external_toolsets(self):
        # Remove previously added toolset rows
        if hasattr(self, "_external_toolset_rows"):
            for row in self._external_toolset_rows:
                self.external_toolsets_container.remove(row)
        self._external_toolset_rows = []

        # Refresh the list
        external_toolsets = [
            toolset for toolset in Settings.current().get_toolsets()
            if toolset.env == ToolsetEnv.EXTERNAL
        ]

        for toolset in external_toolsets:
            action_row = Adw.ActionRow()
            action_row.set_title("External toolset")
            action_row.set_icon_name("preferences-other-symbolic")
            self.external_toolsets_container.insert(action_row, 0)
            self._external_toolset_rows.append(action_row)

    def _load_installations(self, started_installations: list[ToolsetInstallation] = ToolsetInstallation.started_installations):
        # Remove previously added installation rows
        if hasattr(self, "_installation_rows"):
            for row in self._installation_rows:
                self.toolset_installations_list.remove(row)
        self._installation_rows = []

        for installation in started_installations:
            action_row = ToolsetInstallationRow(installation=installation)
            action_row.connect("activated", self.on_installation_pressed)
            self.toolset_installations_list.append(action_row)
            self._installation_rows.append(action_row)

    def on_installation_pressed(self, sender):
        installation = getattr(sender, "installation", None)
        if installation:
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
            Settings.current().add_toolset(Toolset.create_system())
        elif (ts := Settings.current().get_toolset_matching(lambda ts: ts.env == ToolsetEnv.SYSTEM)):
            Settings.current().remove_toolset(ts)

    @Gtk.Template.Callback()
    def on_add_toolset_activated(self, button):
        #self.content_navigation_view.push_view(ToolsetCreateView(), title="New toolset")
        app_event_bus.emit(AppEvents.PRESENT_VIEW, ToolsetCreateView(), "New toolset", 640, 480)

    @Gtk.Template.Callback()
    def on_validate_system_toolset_pressed(self, button):
        # Testing only
        system_toolset = next(
            (toolset for toolset in Settings.current().get_toolsets() if toolset.env == ToolsetEnv.SYSTEM),
            None  # default if no match found
        )
        if not system_toolset:
            raise RuntimeError("System host env not available")
        if not system_toolset.spawned:
            system_toolset.spawn()
        system_toolset.analyze()

class ToolsetInstallationRow(Adw.ActionRow):

    def __init__(self, installation: ToolsetInstallation):
        super().__init__(title=installation.name())
        self.installation = installation
        self.set_activatable(True)
        self._set_status_icon(status=installation.status)
        installation.event_bus.subscribe(
            ToolsetInstallationEvent.STATE_CHANGED,
            self._set_status_icon
        )

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
