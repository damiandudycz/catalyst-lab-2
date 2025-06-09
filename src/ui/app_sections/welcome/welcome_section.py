from gi.repository import Gtk, Adw
from .app_events import AppEvents, app_event_bus
from .app_section import AppSection, app_section
from .repository import Repository, RepositoryEvent

@app_section(title="Welcome", label="Home", icon="go-home-symbolic", show_side_bar=False, order=1_000)
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
        self.content_navigation_view.push_section(AppSection.EnvironmentsSection, wizard_mode=True)

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view
        self.setup_sections_visibility()
        Repository.Toolset.event_bus.subscribe(RepositoryEvent.VALUE_CHANGED, self.setup_sections_visibility)

    def setup_sections_visibility(self, _ = None):
        initial_setup_done = True#Repository.Toolset.value
        self.setup_environments_section.set_visible(not initial_setup_done)
        self.suggested_actions_section.set_visible(initial_setup_done)

