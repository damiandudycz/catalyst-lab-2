from gi.repository import Gtk, GObject
from gi.repository import Adw
from .app_events import AppEvents, app_event_bus
from .app_section import AppSection
from .environment import RuntimeEnv
from .settings import Settings, SettingsEvents
from .root_helper_client import RootHelperClient
from .root_helper_client import root_function

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/app_sections/welcome_section.ui')
class WelcomeSection(Gtk.Box):
    __gtype_name__ = "WelcomeSection"

    actions_section = Gtk.Template.Child()
    action_button_environments = Gtk.Template.Child()
    action_button_builds = Gtk.Template.Child()
    action_button_help = Gtk.Template.Child()
    suggested_actions_section = Gtk.Template.Child()
    setup_environments_section = Gtk.Template.Child()

    @Gtk.Template.Callback()
    def on_environments_row_activated(self, _):
        #app_event_bus.emit(AppEvents.PUSH_VIEW, EnvironmentsSection(), title="Environments")
        #app_event_bus.emit(AppEvents.PUSH_SECTION, AppSection.ENVIRONMENTS)
        #app_event_bus.emit(AppEvents.OPEN_APP_SECTION, AppSection.ENVIRONMENTS)
        test_root._async(lambda x: print(x))
        test_root._async(lambda x: print(x))
        test_root()

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        #print(RootHelperClient.shared().send_command("echo hello back $UID"))
        self.content_navigation_view.push_section(AppSection.ENVIRONMENTS)

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view
        # Setup buttons
        self.setup_sections_visibility()
        # Subscribe to relevant events
        Settings.current.event_bus.subscribe(SettingsEvents.TOOLSETS_CHANGED, self.setup_sections_visibility)

    def setup_sections_visibility(self):
        initial_setup_done = Settings.current.get_toolsets()
        self.setup_environments_section.set_visible(not initial_setup_done)
        self.suggested_actions_section.set_visible(initial_setup_done)

@root_function
def test_root() -> str:
    import time
    time.sleep(5)
    sdsadkjlk
    return "WORKS"

