from gi.repository import Gtk, GObject
from .app_events import EventBus, AppEvents
from .app_section import AppSection
from .environment import RuntimeEnv
from .settings import Settings

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/app_sections/welcome_section.ui')
class WelcomeSection(Gtk.Box):
    __gtype_name__ = "WelcomeSection"

    label = "Welcome"
    icon = "aaa"
    status_label = Gtk.Template.Child()

    @Gtk.Template.Callback()
    def on_start_button_pressed(self, button):
        EventBus.emit(AppEvents.OPEN_APP_SECTION, AppSection.ENVIRONMENTS)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.status_label.set_text(f"Runtime: {RuntimeEnv.current()}, gentoo: {RuntimeEnv.is_running_in_gentoo_host()}")
        Settings.current.save()

