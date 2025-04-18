from gi.repository import Gtk, GObject
from .app_events import EventBus, AppEvents

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/app_sections/welcome_section.ui')
class WelcomeSection(Gtk.Box):
    __gtype_name__ = "WelcomeSection"

    label = "Welcome"
    icon = "aaa"

    @Gtk.Template.Callback()
    def on_start_button_pressed(self, button):
        EventBus.emit(AppEvents.OPEN_ABOUT)
