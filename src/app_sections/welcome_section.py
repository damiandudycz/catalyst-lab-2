from gi.repository import Gtk, GObject

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/app_sections/welcome_section.ui')
class WelcomeSection(Gtk.Box):
    __gtype_name__ = "WelcomeSection"

    label = "Welcome"
    icon = "aaa"

    # Define signals emitted by this widget.
    __gsignals__ = {
        'start-button-pressed': (GObject.SIGNAL_RUN_FIRST, None, (GObject.TYPE_PYOBJECT,))
    }

    @Gtk.Template.Callback()
    def on_start_button_pressed(self, button):
        self.emit("start-button-pressed", self)
