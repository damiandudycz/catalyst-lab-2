from gi.repository import Gtk, GObject
from gi.repository import Adw
from .app_events import EventBus, AppEvents
from .app_section import AppSection
from .environment import RuntimeEnv
from .settings import Settings

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/app_sections/welcome_section.ui')
class WelcomeSection(Gtk.Box):
    __gtype_name__ = "WelcomeSection"

    label = "Welcome"
    icon = "aaa"
    actions_section = Gtk.Template.Child()
    action_button_environments = Gtk.Template.Child()
    action_button_builds = Gtk.Template.Child()
    action_button_help = Gtk.Template.Child()
    suggested_actions_section = Gtk.Template.Child()
    setup_environments_section = Gtk.Template.Child()

    @Gtk.Template.Callback()
    def on_environments_row_activated(self, _):
        #EventBus.emit(AppEvents.PUSH_VIEW, EnvironmentsSection(), title="Environments")
        EventBus.emit(AppEvents.PUSH_SECTION, AppSection.ENVIRONMENTS)
        #self.content_navigation_view.push_section(AppSection.ENVIRONMENTS)

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        EventBus.emit(AppEvents.OPEN_APP_SECTION, AppSection.ENVIRONMENTS)

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(**kwargs)
        self.content_navigation_view = content_navigation_view
        # Setup buttons
        if Settings.current.toolsets:
            # Hides setup environments button at bottom, if some environments are already set
            self.setup_environments_section.set_visible(False)
        else:
            self.suggested_actions_section.set_visible(False)
