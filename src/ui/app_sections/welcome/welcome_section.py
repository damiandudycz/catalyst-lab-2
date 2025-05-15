from gi.repository import Gtk, GObject
from gi.repository import Adw
from .app_events import AppEvents, app_event_bus
from .app_section import AppSection, app_section
from .runtime_env import RuntimeEnv
from .root_helper_client import RootHelperClient
from .settings import Settings, SettingsEvents

@app_section(label="Home", title="Welcome", icon="go-home-symbolic", show_side_bar=False, order=1_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/welcome/welcome_section.ui')
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
        app_event_bus.emit(AppEvents.OPEN_APP_SECTION, AppSection.EnvironmentsSection)

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        self.content_navigation_view.push_section(AppSection.ENVIRONMENTS)

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view
        self.setup_sections_visibility()
        Settings.current().event_bus.subscribe(SettingsEvents.TOOLSETS_CHANGED, self.setup_sections_visibility)

    def setup_sections_visibility(self):
        initial_setup_done = Settings.current().get_toolsets()
        self.setup_environments_section.set_visible(not initial_setup_done)
        self.suggested_actions_section.set_visible(initial_setup_done)

