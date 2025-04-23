from gi.repository import Gtk, GObject
from gi.repository import Adw
from .app_section import AppSection
from .environment import RuntimeEnv, ToolsetEnv, ToolsetEnvHelper
from .settings import Settings, SettingsEvents
from .toolset_env_builder import ToolsetEnvBuilder

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/app_sections/environments_section.ui')
class EnvironmentsSection(Gtk.Box):
    __gtype_name__ = "EnvironmentsSection"

    toolset_system = Gtk.Template.Child()
    toolset_system_checkbox = Gtk.Template.Child()
    toolset_add_button = Gtk.Template.Child()
    external_toolsets_container = Gtk.Template.Child()

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        # Setup host env entry
        self._ignore_toolset_checkbox_signal = False
        if ToolsetEnv.SYSTEM.is_allowed_in_current_host():
            system_toolset_initially_selected = any(toolset.env == ToolsetEnv.SYSTEM for toolset in Settings.current.get_toolsets())
            self._set_toolset_system_checkbox_active(system_toolset_initially_selected)
        else:
            self.toolset_system.set_sensitive(False)
            self._set_toolset_system_checkbox_active(False)
        # Setup external env entries
        self._load_external_toolsets()
        # Subscribe to relevant events
        Settings.current.event_bus.subscribe(SettingsEvents.TOOLSETS_CHANGED, self.toolsets_updated)

    def toolsets_updated(self):
        self._load_external_toolsets()

    def _load_external_toolsets(self):
        # Remove previously added toolset rows
        if hasattr(self, "_external_toolset_rows"):
            for row in self._external_toolset_rows:
                self.external_toolsets_container.remove(row)

        # Refresh the list
        external_toolsets = [
            toolset for toolset in Settings.current.get_toolsets()
            if toolset.env == ToolsetEnv.EXTERNAL
        ]

        self._external_toolset_rows = []

        for toolset in external_toolsets:
            action_row = Adw.ActionRow()
            action_row.set_title("External toolset")
            action_row.set_icon_name("user-desktop-symbolic")

            self.external_toolsets_container.insert(action_row, 0)
            self._external_toolset_rows.append(action_row)

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
            Settings.current.add_toolset(ToolsetEnvHelper.system())
        elif (ts := Settings.current.get_toolset_matching(lambda ts: ts.env == ToolsetEnv.SYSTEM)):
            Settings.current.remove_toolset(ts)

    @Gtk.Template.Callback()
    def on_add_toolset_activated(self, button):
        toolset_env_builder = ToolsetEnvBuilder()
        toolset_env_builder.build_toolset()

        #Settings.current.add_toolset(ToolsetEnvHelper.external("FILE_PATH"))

    # TODO: Make actions for toolset add/remove over some method and not direct access. This method should save by default()
    # TODO: Bind changes in settings to refresh views automatically
