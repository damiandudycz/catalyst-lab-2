from gi.repository import Gtk, Adw
from .app_events import AppEvents, app_event_bus
from .app_section import AppSection, app_section
from .repository import Repository, RepositoryEvent

@app_section(title="Welcome", label="Home", icon="go-home-symbolic", show_in_side_bar=False, show_side_bar=False, order=1_000)
@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/app_sections/welcome/welcome_section.ui')
class WelcomeSection(Gtk.Box):
    __gtype_name__ = "WelcomeSection"

    default_actions_section = Gtk.Template.Child()
    first_run_section = Gtk.Template.Child()

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view
        self.setup_sections_visibility()
        Repository.Toolset.event_bus.subscribe(
            RepositoryEvent.VALUE_CHANGED,
            self.setup_sections_visibility
        )

    def setup_sections_visibility(self, _ = None):
        initial_setup_done = Repository.Settings.value.initial_setup_done
        self.default_actions_section.set_visible(initial_setup_done)
        self.first_run_section.set_visible(not initial_setup_done)

    # --------------------------------------------------------------------------
    # Navigation:

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        self.content_navigation_view.push_section(
            AppSection.EnvironmentsSection
        )
        Repository.Settings.value.initial_setup_done = True

    @Gtk.Template.Callback()
    def on_projects_row_activated(self, _):
        app_event_bus.emit(
            AppEvents.OPEN_APP_SECTION,
            AppSection.ProjectsSection
        )

    @Gtk.Template.Callback()
    def on_builds_row_activated(self, _):
        app_event_bus.emit(
            AppEvents.OPEN_APP_SECTION,
            AppSection.BuildsSection
        )

    @Gtk.Template.Callback()
    def on_help_row_activated(self, _):
        app_event_bus.emit(
            AppEvents.OPEN_APP_SECTION,
            AppSection.HelpSection
        )

