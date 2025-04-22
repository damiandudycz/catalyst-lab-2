from gi.repository import Gtk, GObject
from .app_events import EventBus, AppEvents
from .app_section import AppSection
from .environment import RuntimeEnv, ToolsetEnv, ToolsetEnvHelper
from .settings import Settings

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/app_sections/environments_section.ui')
class EnvironmentsSection(Gtk.Box):
    __gtype_name__ = "EnvironmentsSection"

    toolset_system = Gtk.Template.Child()
    toolset_system_checkbox = Gtk.Template.Child()
    toolset_add_button = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Setup host env entry
        if ToolsetEnv.SYSTEM.is_allowed_in_current_host():
            system_toolset_initially_selected = any(toolset.env == ToolsetEnv.SYSTEM for toolset in Settings.current.toolsets)
            self.toolset_system_checkbox.set_active(system_toolset_initially_selected)
        else:
            self.toolset_system.set_sensitive(False)
            self.toolset_system_checkbox.set_active(False)

    @Gtk.Template.Callback()
    def toolset_system_checkbox_toggled(self, checkbox):
        # Remove all system entries first
        Settings.current.toolsets = [toolset for toolset in Settings.current.toolsets if toolset.env != ToolsetEnv.SYSTEM]
        # If checkbox is checked, place it at the start of the list
        if checkbox.get_active():
            Settings.current.toolsets.insert(0, ToolsetEnvHelper.system())
        Settings.current.save()
